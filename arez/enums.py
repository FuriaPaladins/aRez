﻿from __future__ import annotations

from enum import IntEnum
from collections.abc import Callable, Iterator
from typing import Any, Protocol, Type, TypeVar, cast, TYPE_CHECKING


__all__ = [
    "Rank",
    "Queue",
    "Rarity",
    "Region",
    "Passive",
    "Activity",
    "Language",
    "Platform",
    "DeviceType",
    "AbilityType",
    "PC_PLATFORMS",
]
_T = TypeVar("_T")
_C = TypeVar("_C", bound="Callable[..., Any]")


class _EnumBase(int):
    _name: str
    _value: int
    # These come from _EnumProt
    _name_mapping: dict[str, _EnumBase]
    _value_mapping: dict[int, _EnumBase]
    _member_mapping: dict[str, _EnumBase]
    _short2_mapping: dict[int, str]
    _short3_mapping: dict[int, str]
    _default_value: int
    _immutable: bool

    def __new__(cls, name: str, value: int) -> _EnumBase:
        self = super().__new__(cls, value)
        # ensure we won't end up with underscores in the name
        self._name = name.replace('_', ' ')
        self._value = value
        return self

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}.{self._name.replace(' ', '_')}: {self._value}>"

    @property
    def name(self) -> str:
        """
        The name of the enum member.

        :type: str
        """
        return self._name

    @property
    def value(self) -> int:
        """
        The value of the enum member.

        :type: int
        """
        return self._value

    def __str__(self) -> str:
        """
        Same as accessing the `name` attribute.

        :type: str
        """
        return self._name

    def __int__(self) -> int:
        """
        Same as accessing the `value` attribute.

        :type: int
        """
        return self._value


class _EnumProt(Protocol):
    """
    An internal protocol, describing how the resulting enum class will look like.
    """
    # NOTE: These specify what attributes MAY exist, not necessarily will
    _name_mapping: dict[str, _EnumBase]
    _value_mapping: dict[int, _EnumBase]
    _member_mapping: dict[str, _EnumBase]
    _short2_mapping: dict[int, str]
    _short3_mapping: dict[int, str]
    _default_value: int
    _immutable: bool

    def __new__(  # type: ignore[misc]
        cls: _EnumProt, name: str, value: int, *, default_value: int | None = None
    ) -> _EnumBase | int | str | None:
        ...

    def __call__(self, name: str, value: int) -> _EnumBase:
        ...


class _EnumMeta(type):
    def __new__(
        meta_cls: Type[_EnumMeta],
        name: str,
        bases: tuple[type, ...],
        attrs: dict[str, Any],
        *,
        default_value: int | None = None,
    ):
        # Preprocess special attributes
        new_attrs: dict[str, Any] = {}
        for k, v in attrs.copy().items():
            if k.startswith("_") or hasattr(v, "__get__"):
                # private or special attribute, or descriptor - pass unchanged
                # remove from attrs, so we don't treat them as members later
                new_attrs[k] = attrs.pop(k)

        # Instance our enum
        cls = cast(_EnumProt, super().__new__(meta_cls, name, bases, new_attrs))
        # As long as this attribute exists, the enum can be mutated
        # Use supertype to bypass the check
        super().__setattr__(cls, "_immutable", False)

        # Create enum members
        name_mapping: dict[str, _EnumBase] = {}
        value_mapping: dict[int, _EnumBase] = {}
        member_mapping: dict[str, _EnumBase] = {}
        for k, v in attrs.items():
            if v in value_mapping:
                # existing value, just read it back
                m = value_mapping[v]
            else:
                # create a new value
                m = cls(k, v)
                value_mapping[v] = m
                member_mapping[k] = m
                setattr(cls, k, m)
            k_lower = k.lower()
            name_mapping[k_lower] = m
            if '_' in k:
                # generate a second alias with spaces instead of underscores
                name_mapping[k_lower.replace('_', ' ')] = m
        setattr(cls, "_name_mapping", name_mapping)
        setattr(cls, "_value_mapping", value_mapping)
        setattr(cls, "_member_mapping", member_mapping)
        setattr(cls, "_default_value", default_value)
        delattr(cls, "_immutable")  # finish enum initialization
        return cls

    # Add our special enum member constructor
    def __call__(
        cls: _EnumProt,
        name_or_value: str | int,
        value: int | None = None,
        /, *,
        _return_default: bool = False,
    ) -> _EnumBase | int | str | None:
        if value is not None:
            if getattr(cls, "_immutable", True):
                raise TypeError("Cannot extend enums")
            # new member creation
            return cls.__new__(cls, cast(str, name_or_value), value)
        else:
            # our special lookup
            if isinstance(name_or_value, str):
                member = cls._name_mapping.get(name_or_value.lower())
            elif isinstance(name_or_value, int):
                member = cls._value_mapping.get(name_or_value)
            else:
                member = None
            if member is not None:
                return member
            if _return_default:
                default = cls._default_value
                if default is not None and default in cls._value_mapping:
                    # return the default enum value, if defined
                    return cls._value_mapping[default]
                return name_or_value  # return the input unchanged
            return None

    def __iter__(cls: _EnumProt) -> Iterator[_EnumBase]:
        return iter(cls._member_mapping.values())

    def __delattr__(cls: _EnumProt, name: str):
        if getattr(cls, "_immutable", True):
            raise AttributeError(f"Cannot delete Enum member: {name}")
        type.__delattr__(cls, name)

    def __setattr__(cls: _EnumProt, name: str, value: Any):
        if getattr(cls, "_immutable", True):
            raise AttributeError(f"Cannot reassign Enum member: {name}")
        type.__setattr__(cls, name, value)


# Generate additional aliases for ranks
class _RankMeta(_EnumMeta):
    def __new__(meta_cls: Type[_RankMeta], *args, **kwargs):
        roman_numerals = {
            "i": 1,
            "ii": 2,
            "iii": 3,
            "iv": 4,
            "v": 5,
        }
        cls: _EnumProt = super().__new__(meta_cls, *args, **kwargs)
        more_aliases: dict[str, _EnumBase] = {}
        # generate additional aliases
        for k, m in cls._name_mapping.items():
            if ' ' in k or '_' not in k:
                # skip the already-existing aliases with spaces
                # skip members with no underscores in them
                continue
            name, _, level = k.partition('_')
            level_int = roman_numerals[level]  # change the roman number to int
            more_aliases[f"{name}_{level_int}"] = m  # roman replaced with integer
            more_aliases[f"{name} {level_int}"] = m  # same but with a space
            more_aliases[f"{name}{level_int}"] = m  # no delimiter
        # add the aliases
        cls._name_mapping.update(more_aliases)
        return cls


# Adds short2 and short3 methods to an enum
class _ShortMeta(_EnumMeta):
    def __new__(meta_cls: Type[_ShortMeta], *args, **kwargs):
        cls: _EnumProt = super().__new__(meta_cls, *args, **kwargs)
        short2_mapping: dict[int, str] = {}
        short3_mapping: dict[int, str] = {}
        for k, m in cls._name_mapping.items():
            if len(k) == 2:
                short2_mapping[m.value] = k.upper()
            elif len(k) == 3:
                short3_mapping[m.value] = k.upper()
        type.__setattr__(cls, "_immutable", False)
        setattr(cls, "_short2_mapping", short2_mapping)
        setattr(cls, "_short3_mapping", short3_mapping)
        setattr(cls, "short2", meta_cls.short2)
        setattr(cls, "short3", meta_cls.short3)
        delattr(cls, "_immutable")
        return cls

    @staticmethod
    def short2(self: _EnumBase) -> str:
        """
        Returns a 2-letter uppercase short name for a Region.

        If no such short name exists, return the region ``name`` unchanged.
        """
        value = int(self)
        if value in self._short2_mapping:
            return self._short2_mapping[value]
        return self.name

    @staticmethod
    def short3(self: _EnumBase) -> str:
        """
        Returns a 3-letter uppercase short name for a Region.

        If no such short name exists, return the region ``name`` unchanged.
        """
        value = int(self)
        if value in self._short3_mapping:
            return self._short3_mapping[value]
        return self.name


if TYPE_CHECKING:
    # For typing purposes only
    class Enum(IntEnum):
        def __init__(self, name_or_value: str | int, *, _return_default: bool = False):
            ...

    class EnumRank(IntEnum):
        def __init__(self, name_or_value: str | int, *, _return_default: bool = False):
            ...

    class EnumShort(IntEnum):
        def __init__(self, name_or_value: str | int, *, _return_default: bool = False):
            ...

        def short2(self) -> str:
            ...

        def short3(self) -> str:
            ...
else:
    class Enum(_EnumBase, metaclass=_EnumMeta):
        """
        Represents a basic enum.

        .. note::

            This is here solely for documentation purposes, and shouldn't be used otherwise.

        Parameters
        ----------
        name_or_value : str | int
            The name or value of the enum member you want to get.

        Returns
        -------
        Enum | None
            The matched enum member. `None` is returned if no member could be matched.
        """

    class EnumRank(_EnumBase, metaclass=_RankMeta):
        pass

    class EnumShort(_EnumBase, metaclass=_ShortMeta):
        pass


class Platform(Enum, default_value=0):
    """
    Platform enum. Represents player's platform.

    Inherits from `Enum`.

    Attributes
    ----------
    Unknown
        Unknown platform. You can sometimes get this when the information isn't available.
    PC
        Aliases: ``hirez``, ``standalone``.
    Steam
    PS4
        Aliases: ``psn``, ``playstation``.
    Xbox
        Aliases: ``xb``, ``xboxlive``, ``xbox_live``, ``xboxone``, ``xbox_one``, ``xbox1``,
        ``xbox_1``.
    Facebook
        Aliases: ``fb``.
    Google
    Mixer
    Switch
        Aliases: ``nintendo_switch``.
    Discord
    Epic_Games
        Aliases: ``epic``.
    """
    Unknown         =  0
    PC              =  1
    hirez           =  1
    standalone      =  1
    Steam           =  5
    PS4             =  9
    ps5             =  9
    psn             =  9
    playstation     =  9
    Xbox            = 10
    xb              = 10
    xboxlive        = 10
    xbox_live       = 10
    xboxone         = 10
    xbox_one        = 10
    xbox1           = 10
    xbox_1          = 10
    Facebook        = 12
    fb              = 12
    Google          = 13
    Mixer           = 14
    Switch          = 22
    nintendo_switch = 22
    Discord         = 25
    Epic_Games      = 28
    epic            = 28
    Amazon_Luna     = 30
    luna            = 30


class Region(EnumShort, default_value=0):
    """
    Region enum. Represents player's region.

    Inherits from `Enum`.

    Attributes
    ----------
    Unknown
        Unknown region. You can sometimes get this when the information isn't available.
    North_America
        Aliases: ``na``, ``nam``.
    Europe
        Aliases: ``eu``, ``eur``.
    Australia
        Aliases: ``au``, ``aus``, ``oc``, ``oce``, ``oceania``.
    Brazil
        Aliases: ``br``, ``bra``.
    Latin_America_North
        Aliases: ``la``, ``lan``, ``latam``.
    Southeast_Asia
        Aliases: ``sa``, ``sea``.
    Japan
        Aliases: ``jp``, ``jpn``.
    """
    Unknown             = 0
    North_America       = 1
    na                  = 1
    nam                 = 1
    Europe              = 2
    eu                  = 2
    eur                 = 2
    Australia           = 3
    au                  = 3
    aus                 = 3
    oc                  = 3
    oce                 = 3
    oceania             = 3
    Brazil              = 4
    br                  = 4
    bra                 = 4
    Latin_America_North = 5
    la                  = 5
    lan                 = 5
    latam               = 5
    Southeast_Asia      = 6
    sa                  = 6
    sea                 = 6
    Japan               = 7
    jp                  = 7
    jpn                 = 7


class Language(EnumShort):
    """
    Language enum. Represents the response language.

    Inherits from `Enum`.

    Attributes
    ----------
    English
        Aliases: ``en``, ``eng``.
    German
        Aliases: ``de``, ``ger``.
    French
        Aliases: ``fr``, ``fre``.
    Chinese
        Aliases: ``zh``, ``chi``.
    Spanish
        Aliases: ``es``, ``spa``.
    Portuguese
        Aliases: ``pt``, ``por``.
    Russian
        Aliases: ``ru``, ``rus``.
    Polish
        Aliases: ``pl``, ``pol``.
    Turkish
        Aliases: ``tr``, ``tur``.
    """
    # Unknown  =  0
    English    =  1
    en         =  1
    eng        =  1
    German     =  2
    de         =  2
    ger        =  2
    French     =  3
    fr         =  3
    fre        =  3
    Chinese    =  5
    zh         =  5
    chi        =  5
    # Spanish  =  7  # old spanish - it seems like this language isn't used that much
    # spanish  =  7  # over the #9 one, and is full of mostly outdated data
    # es       =  7
    Spanish    =  9  # old Latin America
    es         =  9
    spa        =  9
    Portuguese = 10
    pt         = 10
    por        = 10
    Russian    = 11
    ru         = 11
    rus        = 11
    Polish     = 12
    pl         = 12
    pol        = 12
    Turkish    = 13
    tr         = 13
    tur        = 13


class Queue(Enum, default_value=0):
    """
    Queue enum. Represents a match queue.

    Inherits from `Enum`.

    List of custom queue attributes: ``Custom_Ascension_Peak``, ``Custom_Bazaar``,
    ``Custom_Brightmarsh``, ``Custom_Fish_Market``, ``Custom_Frog_Isle``, ``Custom_Frozen_Guard``,
    ``Custom_Ice_Mines``, ``Custom_Jaguar_Falls``, ``Custom_Serpent_Beach``,
    ``Custom_Shattered_Desert``, ``Custom_Splitstone_Quary``, ``Custom_Stone_Keep``,
    ``Custom_Timber_Mill``, ``Custom_Warders_Gate``, ``Custom_Foremans_Rise_Onslaught``,
    ``Custom_Magistrates_Archives_Onslaught``, ``Custom_Marauders_Port_Onslaught``,
    ``Custom_Primal_Court_Onslaught``, ``Custom_Abyss_TDM``, ``Custom_Dragon_Arena_TDM``,
    ``Custom_Foremans_Rise_TDM``, ``Custom_Magistrates_Archives_TDM``,
    ``Custom_Snowfall_Junction_TDM``, ``Custom_Throne_TDM``, ``Custom_Trade_District_TDM``,
    ``Custom_Magistrates_Archives_KotH``, ``Custom_Snowfall_Junction_KotH``,
    ``Custom_Trade_District_KotH``.

    Attributes
    ----------
    Unknown
        Unknown queue. You can sometimes get this when the information isn't available.
    Casual_Siege
        Aliases: ``casual``, ``siege``.
    Team_Deathmatch
        Aliases: ``deathmatch``, ``tdm``.
    Onslaught
    Ranked
        Aliases: ``competitive``, ``rank``, ``comp``.
    Shooting_Range
        Aliases: ``range``.
    Training_Siege
        Aliases: ``bot_siege``.
    Training_Onslaught
        Aliases: ``bot_onslaught``.
    Training_Team_Deathmatch
        Aliases: ``bot_team_deathmatch``, ``bot_deathmatch``, ``bot_tdm``.
    Test_Maps
        Aliases: ``test``.
    """
    Unknown                  = 0
    Casual_Siege             = 424
    casual                   = 424
    siege                    = 424
    Team_Deathmatch          = 10296
    deathmatch               = 10296
    tdm                      = 10296
    Onslaught                = 452
    Ranked                   = 486
    competitive              = 486
    comp                     = 486
    rank                     = 486
    Shooting_Range           = 434
    range                    = 434
    Training_Siege           = 425
    bot_siege                = 425
    Training_Onslaught       = 453
    bot_onslaught            = 453
    Training_Team_Deathmatch = 10297
    bot_team_deathmatch      = 10297
    bot_deathmatch           = 10297
    bot_tdm                  = 10297
    Test_Maps                = 445
    test                     = 445
    # LTMs
    Payload                 = 10279
    Cards_To_The_Max        = 10284
    Floor_is_Lava           = 10287
    Siege_of_Ascension_Peak = 10285
    Health_Drops            = 10235
    # Old/replaced queues
    Classic_Team_Deathmatch          = 469
    Classic_Training_Team_Deathmatch = 470
    # Customs
    Custom_Bazaar                         = 426
    Custom_Timber_Mill                    = 430
    Custom_Fish_Market                    = 431
    Custom_Frozen_Guard                   = 432
    Custom_Frog_Isle                      = 433
    Custom_Jaguar_Falls                   = 438
    Custom_Ice_Mines                      = 439
    Custom_Serpent_Beach                  = 440
    Custom_Snowfall_Junction_TDM          = 454
    Custom_Primal_Court_Onslaught         = 455
    Custom_Brightmarsh                    = 458
    Custom_Splitstone_Quary               = 459
    Custom_Foremans_Rise_Onslaught        = 462
    Custom_Magistrates_Archives_Onslaught = 464
    Custom_Trade_District_TDM             = 468
    Custom_Foremans_Rise_TDM              = 471
    Custom_Magistrates_Archives_TDM       = 472
    Custom_Ascension_Peak                 = 473
    Custom_Abyss_TDM                      = 479
    Custom_Throne_TDM                     = 480
    Custom_Marauders_Port_Onslaught       = 483
    Custom_Dragon_Arena_TDM               = 484
    Custom_Warders_Gate                   = 485
    Custom_Shattered_Desert               = 487
    Custom_Magistrates_Archives_KotH      = 10200
    Custom_Snowfall_Junction_KotH         = 10201
    Custom_Trade_District_KotH            = 10202
    Custom_Stone_Keep_Night               = 10210
    Custom_Stone_Keep_Day                 = 10239

    def is_casual(self) -> bool:
        """
        Checks if this queue is considered "casual".
        Casual queues are the ones accessible from the main queue screen.

        .. note::

            This does not include custom or training matches.

        :type: bool
        """
        return self in (
            self.Casual_Siege,
            self.Onslaught,
            self.Team_Deathmatch,
            self.Test_Maps,
            self.Classic_Team_Deathmatch,
        ) or self.is_ltm()

    def is_ranked(self) -> bool:
        """
        Checks if this queue is considered "ranked" or "competitive".

        :type: bool
        """
        return self is self.Ranked

    is_competitive = is_ranked

    def is_training(self) -> bool:
        """
        Checks if this queue is considered "training".

        :type: bool
        """
        return self in (
            self.Shooting_Range,
            self.Training_Siege,
            self.Training_Onslaught,
            self.Training_Team_Deathmatch,
            self.Classic_Training_Team_Deathmatch,
        )

    def is_custom(self) -> bool:
        """
        Checks if this queue is considered "custom".

        :type: bool
        """
        return self.name.startswith("Custom")

    def is_siege(self) -> bool:
        """
        Checks if this queue contains "siege" game mode.

        :type: bool
        """
        return self.is_ranked() or self in (
            self.Casual_Siege,
            self.Training_Siege,
            # Custom Siege
            self.Custom_Ascension_Peak,
            self.Custom_Bazaar,
            self.Custom_Brightmarsh,
            self.Custom_Fish_Market,
            self.Custom_Frog_Isle,
            self.Custom_Frozen_Guard,
            self.Custom_Ice_Mines,
            self.Custom_Jaguar_Falls,
            self.Custom_Serpent_Beach,
            self.Custom_Shattered_Desert,
            self.Custom_Splitstone_Quary,
            self.Custom_Stone_Keep_Day,
            self.Custom_Stone_Keep_Night,
            self.Custom_Timber_Mill,
            self.Custom_Warders_Gate,
        )

    def is_onslaught(self) -> bool:
        """
        Checks if this queue contains "onslaught" game mode.

        :type: bool
        """
        return self in (
            self.Onslaught,
            self.Training_Onslaught,
            self.Custom_Foremans_Rise_Onslaught,
            self.Custom_Magistrates_Archives_Onslaught,
            self.Custom_Marauders_Port_Onslaught,
            self.Custom_Primal_Court_Onslaught,
        )

    def is_tdm(self) -> bool:
        """
        Checks if this queue contains "team deathmatch" game mode.

        :type: bool
        """
        return self in (
            self.Team_Deathmatch,
            self.Classic_Team_Deathmatch,
            # Custom TDM
            self.Custom_Abyss_TDM,
            self.Custom_Dragon_Arena_TDM,
            self.Custom_Foremans_Rise_TDM,
            self.Custom_Magistrates_Archives_TDM,
            self.Custom_Snowfall_Junction_TDM,
            self.Custom_Throne_TDM,
            self.Custom_Trade_District_TDM,
        )

    def is_koth(self) -> bool:
        """
        Checks if this queue contains "king of the hill" game mode.

        .. note::

            This does include the `Onslaught` queue, regardless if the match played was normal
            onslaught or not.

        :type: bool
        """
        return self in (
            self.Onslaught,
            # Custom KotH
            self.Custom_Magistrates_Archives_KotH,
            self.Custom_Snowfall_Junction_KotH,
            self.Custom_Trade_District_KotH,
        )

    def is_ltm(self) -> bool:
        """
        Checks if this queue is a Limited Time Mode.
        These game modes are cycles through in and out as time goes on.

        :type: bool
        """
        return self in (
            self.Payload,
            self.Cards_To_The_Max,
            self.Floor_is_Lava,
            self.Siege_of_Ascension_Peak,
            self.Health_Drops,
        )


class Rank(EnumRank):
    """
    Rank enum. Represents player's rank.

    Inherits from `Enum`.

    All attributes include an alias consisting of their name and a single digit
    representing the rank's level, alternatively with and without the dividing space existing
    or being replaced with an underscore. For example, all of these will result in the
    ``Gold IV`` rank: ``gold_iv``, ``gold iv``, ``gold_4``, ``gold 4``, ``gold4``.

    List of all attributes: ``Qualifying``, ``Bronze_V``, ``Bronze_IV``, ``Bronze_III``,
    ``Bronze_II``, ``Bronze_I``, ``Silver_V``, ``Silver_IV``, ``Silver_III``, ``Silver_II``,
    ``Silver_I``, ``Gold_V``, ``Gold_IV``, ``Gold_III``, ``Gold_II``, ``Gold_I``, ``Platinum_V``,
    ``Platinum_IV``, ``Platinum_III``, ``Platinum_II``, ``Platinum_I``, ``Diamond_V``,
    ``Diamond_IV``, ``Diamond_III``, ``Diamond_II``, ``Diamond_I``, ``Master``, ``Grandmaster``.
    """
    _ROMAN2INT = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}

    Qualifying   =  0
    Bronze_V     =  1
    Bronze_IV    =  2
    Bronze_III   =  3
    Bronze_II    =  4
    Bronze_I     =  5
    Silver_V     =  6
    Silver_IV    =  7
    Silver_III   =  8
    Silver_II    =  9
    Silver_I     = 10
    Gold_V       = 11
    Gold_IV      = 12
    Gold_III     = 13
    Gold_II      = 14
    Gold_I       = 15
    Platinum_V   = 16
    Platinum_IV  = 17
    Platinum_III = 18
    Platinum_II  = 19
    Platinum_I   = 20
    Diamond_V    = 21
    Diamond_IV   = 22
    Diamond_III  = 23
    Diamond_II   = 24
    Diamond_I    = 25
    Master       = 26
    Grandmaster  = 27

    @property
    def alt_name(self) -> str:
        """
        str: Returns an alternative name of a rank, with the roman numeral replaced
        with an integer.

        Example: ``Silver IV`` -> ``Silver 4``.
        """
        if ' ' in self.name:
            tier, _, division = self.name.partition(' ')
            return f"{tier} {self._ROMAN2INT[division]}"
        return self.name

    @property
    def tier(self) -> str:
        """
        str: Returns the rank's tier, one of: ``Qualifying``, ``Bronze``, ``Silver``, ``Gold``,
        ``Platinum``, ``Diamond``, ``Master`` or ``Grandmaster``.
        """
        if ' ' in self.name:
            tier, _, division = self.name.partition(' ')
            return tier
        return self.name

    @property
    def division(self) -> str:
        """
        str: Returns the rank's division, one of: ``I``, ``II``, ``III``, ``IV`` or ``V``.\n
        If the rank has no divisions, returns the name unchanged:
        ``Qualifying``, ``Master`` or ``Grandmaster``.
        """
        if ' ' in self.name:
            tier, _, division = self.name.partition(' ')
            return division
        return self.name

    @property
    def alt_division(self) -> str:
        """
        str: Returns the rank's division as an integer, one of: ``1``, ``2``, ``3``,
        ``4`` or ``5``.\n
        If the rank has no divisions, returns the name unchanged:
        ``Qualifying``, ``Master`` or ``Grandmaster``.
        """
        if ' ' in self.name:
            tier, _, division = self.name.partition(' ')
            return self._ROMAN2INT[division]
        return self.name


class Passive(Enum):
    """
    Passive enum. Represents an in-match passive ability. Currently applies only to Octavia.
    Available at: `MatchLoadout.passive`.

    Inherits from `Enum`.

    Attributes
    """
    # Some champions appear to have other devices stored in there,
    # but we don't care about that for now
    # Wall_Climb = 23461  # Koga's Wall Climb
    # DR = 26716  # Yagorath's DR?
    # Octavia's passives
    Shield   = 26883
    Credit   = 27051
    Cooldown = 27053
    Ultimate = 27052


class DeviceType(Enum, default_value=0):
    """
    DeviceType enum. Represents a type of device: talent, card, shop item, etc.

    Inherits from `Enum`.

    Attributes
    ----------
    Undefined
        Represents an undefined device type. Devices with this type are usually (often unused)
        talents or cards that couldn't be determined as valid.
    Item
        The device of this type is a Shop Item.
    Card
        The device of this type is a Card.
    Talent
        The device of this type is a Talent.
    """
    Undefined = 0
    Item      = 1
    Card      = 2
    Talent    = 3


class Rarity(Enum):
    """
    Rarity enum. Represents a skin or card rarity.

    Inherits from `Enum`.

    Attributes
    ----------
    Default
    Common
    Uncommon
    Rare
    Epic
    Legendary
    Unlimited
    Limited
    """
    Default   = 0
    Common    = 1
    Uncommon  = 2
    Rare      = 3
    Epic      = 4
    Legendary = 5
    Unlimited = 6
    Limited   = 7


class AbilityType(Enum, default_value=0):
    """
    AbilityType enum. Represents a type of an ability.

    Currently only damage types are supported.

    Inherits from `Enum`.

    Attributes
    ----------
    Undefined
        Represents an undefined ability type. Those abilities often deal no damage,
        or serve another purpose that doesn't involve them doing so.
    Direct_Damage
        The ability does Direct Damage.\n
        Aliases: ``direct``.
    Area_Damage
        The ability does Area Damage.\n
        Aliases: ``aoe``.
    """
    Undefined     = 0
    Direct_Damage = 1
    direct        = 1
    Area_Damage   = 2
    aoe           = 2


class Activity(Enum, default_value=5):
    """
    Activity enum. Represents player's in-game status.

    Inherits from `Enum`.

    Attributes
    ----------
    Offline
        The player is currently offline.
    In_Lobby
        The player is in the post-match lobby.
    Character_Selection
        The player is currently on the character selection screen before a match.
    In_Match
        The player is currently in a live match.
    Online
        The player is currently online, most likely on the main menu screen.
    Unknown
        The player's status is unknown.
    """
    Offline             = 0
    In_Lobby            = 1
    Character_Selection = 2
    In_Match            = 3
    Online              = 4
    Unknown             = 5


# PC platforms constant
PC_PLATFORMS = (Platform.PC, Platform.Steam, Platform.Discord)
