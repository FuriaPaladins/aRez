from __future__ import annotations

import re
from typing import Literal, cast, TYPE_CHECKING

from .utils import Lookup
from .mixins import CacheClient, CacheObject
from .enums import DeviceType, AbilityType, Rarity

if TYPE_CHECKING:
    from . import responses
    from .items import Device
    from .enums import Language
    from .cache import DataCache


__all__ = [
    "Skin",
    "Ability",
    "Champion",
]


def _card_ability_sort(card: Device) -> str:
    ability = card.ability
    if type(ability) == CacheObject:
        return f"z{ability.name}"  # push the card to the very end
    return ability.name


class Ability(CacheObject):
    """
    Represents a Champion's Ability.

    You can find these on the `Champion.abilities` attribute.

    Inherits from `CacheObject`.

    Attributes
    ----------
    name : str
        The name of the ability.
    id : int
        The ID of the ability.
    champion : Champion
        The champion this ability belongs to.
    description : str
        The description of the ability.
    type : AbilityType
        The type of the ability (currently only damage type).
    cooldown : int
        The ability's cooldown, in seconds.
    icon_url : str
        A URL of this ability's icon.
    """
    _desc_pattern = re.compile(r" ?<br>(?:<br>)? ?")  # replace the <br> tags with a new line

    def __init__(self, champion: Champion, ability_data: responses.AbilityObject):
        super().__init__(id=ability_data["Id"], name=ability_data["Summary"])
        self.champion = champion
        desc = ability_data["Description"].strip().replace('\r', '')
        self.description: str = self._desc_pattern.sub('\n', desc)
        self.type = AbilityType(ability_data["damageType"], _return_default=True)
        self.cooldown: int = ability_data["rechargeSeconds"]
        self.icon_url: str = ability_data["URL"]

    __hash__ = CacheObject.__hash__


class Skin(CacheObject):
    """
    Represents a Champion's Skin and it's information.

    You can get these from the `Champion.get_skins` method,
    as well as find on various other objects returned from the API.

    Inherits from `CacheObject`.

    Attributes
    ----------
    name : str
        The name of the skin.
    id : int
        The ID of the skin.
    champion : Champion
        The champion this skin belongs to.
    rarity : Rarity
        The skin's rarity.
    """
    def __init__(self, champion: Champion, skin_data: responses.ChampionSkinObject):
        # pre-process champion and skin name
        self.champion: Champion = champion
        skin_name = skin_data["skin_name"]
        if skin_name.endswith(self.champion.name):
            skin_name = skin_name[:-len(self.champion.name)].strip()
        super().__init__(id=skin_data["skin_id2"], name=skin_name)
        rarity: str = skin_data["rarity"]
        self.rarity: Rarity
        if rarity:  # not an empty string
            self.rarity = Rarity(rarity, _return_default=True)
        else:
            self.rarity = Rarity.Default

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}: {self._name} {self.champion.name}"
            f"({self.rarity.name}, {self._id})"
        )


class Champion(CacheObject, CacheClient):
    """
    Represents a Champion and it's information.

    You can find these on the `CacheEntry.champions` attribute,
    as well as various other objects returned from the API.

    Inherits from `CacheObject`.

    .. note::

        An object of this class can be `False` in a boolean context, if it's internal state
        is deemed incomplete or corrupted. For the internal state to be considered valid, there has
        to be exactly 16 cards and 3 talents assigned to the champion. If you don't plan on
        accessing / processing those, you can use the ``is not None`` in the check instead.
        Examples:

        .. code-block:: py

            if champion:
                # champion exists and is valid
            if not champion:
                # champion doesn't exist, or exists in an invalid state
            if champion is not None:
                # champion exists but might be invalid
            if champion is None:
                # champion doesn't exist

    Attributes
    ----------
    name : str
        The name of the champion.
    id : int
        The ID of the champion.
    title : str
        The champion's title.
    role : Literal["Front Line", "Support", "Damage", "Flank"]
        The champion's role.
    lore : str
        The champion's lore.
    icon_url : str
        A URL of this champion's icon.
    health : int
        The amount of health points this champion has at base.
    speed : int
        The champion's speed.
    abilities : Lookup[Ability]
        An object that lets you iterate over all abilities this champion has.\n
        Use ``list(...)`` to get a list instead.

        .. note::

            Some champions may have more than 5 abilities - this will happen if one of their
            abilities allows switching other abilities between their states.

    talents : Lookup[Device]
        An object that lets you iterate over all talents this champion has.\n
        Use ``list(...)`` to get a list instead.
    cards : Lookup[Device]
        An iterator that lets you iterate over all cards this champion has.\n
        Use ``list(...)`` to get a list instead.
    skins : Lookup[Skin]
        An object that lets you iterate over all skins this champion has.\n
        Use ``list(...)`` to get a list instead.
    """
    _name_pattern = re.compile(r'([a-z ]+)(?:/\w+)? \(([a-z ]+)\)', re.I)
    _desc_pattern = re.compile(r'([A-Z][a-zA-Z ]+): ([\w\s\-\'%,.]+)(?:<br><br>|[\r\n]?\n|$)')
    _url_pattern = re.compile(r'([a-z\-]+)(?=\.(?:jpg|png))')

    def __init__(
        self,
        cache: DataCache,
        language: Language,
        champion_data: responses.ChampionObject,
        devices: list[Device],
        skins_data: list[responses.ChampionSkinObject],
    ):
        CacheClient.__init__(self, cache)
        CacheObject.__init__(self, id=champion_data["id"], name=champion_data["Name"])
        self._language = language
        self.title: str = champion_data["Title"]
        self.role = cast(
            Literal["Front Line", "Support", "Damage", "Flank"],
            champion_data["Roles"][9:].replace("er", ""),
        )
        self.icon_url: str = champion_data["ChampionIcon_URL"]
        self.lore: str = champion_data["Lore"]
        self.health: int = champion_data["Health"]
        self.speed: int = champion_data["Speed"]

        # Abilities
        abilities = []
        for i in range(1, 6):
            ability_data = champion_data[f"Ability_{i}"]  # type: ignore[literal-required]
            # see if this is a composite ability
            match = self._name_pattern.match(ability_data["Summary"])
            if match:
                # yes - we need to split the data into two sets
                composites: dict[str, responses.AbilityObject] = {}
                name1, name2 = match.groups()
                composites[name1] = {"Summary": name1}  # type: ignore[typeddict-item]
                composites[name2] = {"Summary": name2}  # type: ignore[typeddict-item]
                descs = self._desc_pattern.findall(ability_data["Description"])
                for ability_name, ability_desc in descs:
                    ability_dict = composites.get(ability_name)
                    if ability_dict is None:
                        continue
                    ability_dict["Description"] = ability_desc
                    # modify the URL
                    ability_dict["URL"] = self._url_pattern.sub(
                        ability_name.lower().replace(' ', '-'), ability_data["URL"]
                    )
                    # copy the rest of attributes
                    ability_dict["Id"] = ability_data["Id"]
                    ability_dict["damageType"] = ability_data["damageType"]
                    ability_dict["rechargeSeconds"] = ability_data["rechargeSeconds"]
                    # add the ability
                    abilities.append(Ability(self, ability_dict))
            else:
                # nope - just append it
                abilities.append(Ability(self, ability_data))
        self.abilities: Lookup[Ability, Ability] = Lookup(abilities)

        # Talents and Cards
        cards: list[Device] = []
        talents: list[Device] = []
        for d in devices:
            if d.type == DeviceType.Card:
                cards.append(d)
            elif d.type == DeviceType.Talent:  # pragma: no branch
                talents.append(d)
            d._attach_champion(self)  # requires the abilities to exist already
        talents.sort(key=lambda d: d.unlocked_at)
        cards.sort(key=lambda d: d.name)
        cards.sort(key=_card_ability_sort)
        self.cards: Lookup[Device, Device] = Lookup(cards)
        self.talents: Lookup[Device, Device] = Lookup(talents)

        # Skins
        self.skins: Lookup[Skin, Skin] = Lookup(
            sorted((Skin(self, d) for d in skins_data), key=lambda s: s.rarity.value)
        )

    __hash__ = CacheObject.__hash__

    def __bool__(self) -> bool:
        return len(self.cards) == 16 and len(self.talents) == 3

    async def get_skins(self) -> list[Skin]:
        """
        Returns a list of skins this champion has.

        .. note::

            This information is cached under the `skins` attribute.

        Returns
        -------
        list[Skin]
            The list of skins available for this champion.
        """
        response = await self._api.request("getchampionskins", self.id, self._language.value)
        self.skins = Lookup(
            sorted((Skin(self, skin_data) for skin_data in response), key=lambda s: s.rarity.value)
        )
        return list(self.skins)
