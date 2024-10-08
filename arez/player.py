from __future__ import annotations

import logging
from datetime import datetime
from functools import cached_property
from typing import Sequence, SupportsInt, TYPE_CHECKING

from .items import Loadout
from .match import PartialMatch
from .status import PlayerStatus
from .exceptions import Private, NotFound
from .enums import Language, Platform, Region, Queue
from .stats import Stats, RankedStats, ChampionStats
from .mixins import CacheClient, CacheObject, Expandable
from .utils import _convert_timestamp, Duration, Lookup, LookupGroup

if TYPE_CHECKING:
    from . import responses
    from .cache import DataCache
    from .champion import Champion


__all__ = ["PartialPlayer", "Player"]
logger = logging.getLogger(__package__)


class PartialPlayer(Expandable["Player"], CacheClient):
    """
    This object stores basic information about a player, such as their Player ID, Player Name
    and their Platform. Depending on the way it was created, only the Player ID is guaranteed
    to exist - both ``name`` and ``platform`` can be an empty string and `Platform.Unknown`
    respectively.

    To ensure all attributes are filled up correctly before processing, you can upgrade this
    object to the full `Player` one first, by awaiting on it and using the result:

    .. code-block:: py

        player = await partial_player

    .. note::

        In addition to the exceptions specified below, each API request can result
        in the following exceptions being raised:

        `Unavailable`
            The API is currently unavailable.
        `LimitReached`
            Your daily limit of requests has been reached.
        `HTTPException`
            Fetching the information requested failed due to connection problems.
    """
    def __init__(
        self,
        api: DataCache,
        *,
        id: SupportsInt,
        name: str = '',
        platform: str | int = 0,
        private: bool = False,
    ):
        super().__init__(api)
        self._id: int = int(id)
        self._name: str = str(name)
        self._hash: int | None = None
        if isinstance(platform, str) and platform.isdecimal():
            platform = int(platform)
        self._platform = Platform(platform, _return_default=True)
        self._private = bool(private)
        logger.debug(
            f"Player(id={self._id}, name={self._name}, platform={self._platform.name}, "
            f"private={self._private}) -> created"
        )

    async def _expand(self) -> Player:
        """
        Upgrades this object to a full `Player` one, refreshing and ensuring information stored.

        Uses up a single request.

        Returns
        -------
        Player
            A full player object with all fields filled up, for the same player.

        Raises
        ------
        NotFound
            The player's profile doesn't exist / couldn't be found.
        Private
            The player's profile was private.
        """
        if self.private:
            raise Private
        logger.info(f"Player(id={self._id}).expand()")
        player_list = await self._api.request("getplayer", self._id)
        if not player_list:
            raise NotFound("Player")
        player_data = player_list[0]
        if player_data["ret_msg"]:
            raise Private
        return Player(self._api, player_data)

    def __eq__(self, other) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self._id != 0 and other._id != 0 and self._id == other._id

    def __hash__(self) -> int:
        if self._hash is None:
            if self._id != 0:
                # if it's not zero, just hash it
                self._hash = hash(("Player", self._id))
            else:
                # with an ID of zero, fall back to identity object hash
                self._hash = object.__hash__(self)
        return self._hash

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}: {self._name}({self._id} / {self.platform.name})"

    @property
    def id(self) -> int:
        """
        Unique ID of the player. A value of ``0`` indicates a private player account,
        and shouldn't be used to distinguish between different players.

        :type: int
        """
        return self._id

    @property
    def name(self) -> str:
        """
        Name of the player.

        :type: str
        """
        return self._name

    @property
    def platform(self) -> Platform:
        """
        The player's platform.

        :type: Platform
        """
        return self._platform

    @cached_property
    def private(self) -> bool:
        """
        Checks to see if this profile is private or not.

        Trying to fetch any information for a private profile will raise the `Private` exception.

        Returns
        -------
        bool
            `True` if this player profile is considered private, `False` otherwise.
        """
        return self._private or self._id == 0

    async def get_status(self) -> PlayerStatus:
        """
        Fetches the player's current status.

        Uses up a single request.

        Returns
        -------
        PlayerStatus
            The player's status.

        Raises
        ------
        Private
            The player's profile was private.
        NotFound
            The player's status couldn't be found.
        """
        if self.private:
            raise Private
        logger.info(f"Player(id={self._id}).get_status()")
        response = await self._api.request("getplayerstatus", self._id)
        if not response or response[0]["status"] == 5:
            raise NotFound("Player status")
        return PlayerStatus(self, response[0])

    async def get_friends(self) -> tuple[list[PartialPlayer], list[PartialPlayer]]:
        """
        Fetches the player's friend list.

        Uses up a single request.

        Returns
        -------
        tuple[list[PartialPlayer], list[PartialPlayer]]
            A list of players this player is friends with.\n
            Some players might be missing if their profile is set as private.\n
            First element is friends, second is blocked players.

        Raises
        ------
        Private
            The player's profile was private.
        """
        if self.private:
            raise Private
        logger.info(f"Player(id={self._id}).get_friends()")
        response = await self._api.request("getfriends", self._id)
        return [
            PartialPlayer(self._api, id=p["player_id"], name=p["name"], platform=p["portal_id"])
            for p in response
            if p["friend_flags"] == "1"  # yes, apparently it's a string
        ], [
            PartialPlayer(self._api, id=p["player_id"], name=p["name"], platform=p["portal_id"])
            for p in response
            if p["friend_flags"] == "32"
        ]

    async def get_loadouts(
        self, language: Language | None = None
    ) -> LookupGroup[Champion | CacheObject, Loadout]:
        """
        Fetches the player's loadouts.

        Uses up a single request.

        .. note::

            The `LookupGroup` class provides an easy way of searching for loadouts
            for a particular champion, based on the champion's name or ID.
            You can also obtain a list of all loadouts instead.

            Please see the example code below:

            .. code-block:: py

                player: PartialPlayer
                loadouts: LookupGroup[Champion, Loadout] = await player.get_loadouts()

                # obtain a list of all loadouts (for all champions)
                list_loadouts = list(loadouts)
                # get a list of loadouts for a particular champion
                champion_loadouts = loadouts.get("Androxus")
                # fuzzy name matching
                champion_loadouts = loadouts.get_fuzzy("andro")

        Parameters
        ----------
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.

        Returns
        -------
        LookupGroup[Champion | CacheObject, Loadout]
            An object that lets you iterate over and lookup player's loadouts for each champion.

        Raises
        ------
        Private
            The player's profile was private.
        """
        if self.private:
            raise Private
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._api._default_language
        cache_entry = await self._api._fetch_entry(language)
        logger.info(f"Player(id={self._id}).get_loadouts(language={language.name})")
        response = await self._api.request("getplayerloadouts", self._id, language.value)
        if not response or response and not response[0]["playerId"]:
            return LookupGroup([])
        return LookupGroup(
            (Loadout(self, cache_entry, loadout_data) for loadout_data in response),
            key=lambda l: l.champion,
        )

    async def get_champion_stats(
        self, language: Language | None = None, *, queue: Queue | None = None
    ) -> Lookup[Champion | CacheObject, ChampionStats]:
        """
        Fetches the player's champion statistics.

        Uses up a single request.

        .. note::

            The `Lookup` class provides an easy way of searching for particular statistics,
            based on their associated champion's name or ID. You can also obtain a list
            of all champion statistics instead.

            Please see the example code below:

            .. code-block:: py

                player: PartialPlayer
                stats: Lookup[Champion, ChampionStats] = await player.get_champion_stats()

                # obtain a list of stats for all champion
                list_stats = list(stats)
                # get the stats object for a particular champion
                champion_stats = stats.get("Androxus")
                # fuzzy name matching
                champion_stats = stats.get_fuzzy("andro")

        Parameters
        ----------
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.
        queue : Queue | None
            The queue you want to filter the returned stats to.\n
            Defaults to all queues.

        Returns
        -------
        Lookup[Champion | CacheObject, ChampionStats]
            An object that lets you iterate over and lookup each champion's statistics,
            one for each played champion.\n
            Some statistics may be missing for champions the player haven't played yet.

        Raises
        ------
        Private
            The player's profile was private.
        """
        if self.private:
            raise Private
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._api._default_language
        cache_entry = await self._api._fetch_entry(language)
        logger.info(f"Player(id={self._id}).get_champion_stats(language={language.name})")
        response: Sequence[responses.ChampionRankObject | responses.ChampionQueueRankObject]
        if queue is None:
            response = await self._api.request("getgodranks", self._id)
        else:
            response = await self._api.request("getqueuestats", self._id, queue.value)
        return Lookup(
            (ChampionStats(self, cache_entry, stats_data, queue) for stats_data in response),
            key=lambda s: s.champion,
        )

    async def get_match_history(self, language: Language | None = None) -> list[PartialMatch]:
        """
        Fetches player's match history.

        Uses up a single request.

        .. note::

            The returned list can be empty (or contain less elements) if the player haven't played
            any matches yet, or their last played match is over 30 days old.

        Parameters
        ----------
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.

        Returns
        -------
        list[PartialMatch]
            A list of up to 50 partial matches, containing statistics for the current player only.

        Raises
        ------
        Private
            The player's profile was private.
        """
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if self.private:
            raise Private
        if language is None:
            language = self._api._default_language
        cache_entry = await self._api._fetch_entry(language)
        logger.info(f"Player(id={self._id}).get_match_history(language={language.name})")
        response = await self._api.request("getmatchhistory", self._id)
        if not response or response and response[0]["ret_msg"]:
            return []
        return [PartialMatch(self, language, cache_entry, match_data) for match_data in response]


class Player(PartialPlayer):
    """
    A full Player object, containing all information about a player.
    You can get this from the `PaladinsAPI.get_player` and `PaladinsAPI.get_players` methods,
    as well as from upgrading a `PartialPlayer` object, by awaiting on it.

    .. note::

        This class inherits from `PartialPlayer`, so all of it's methods should be present
        here as well.

    Attributes
    ----------
    active_player : PartialPlayer | None
        The current active player between merged profiles.\n
        `None` if the current profile is the active profile.
    merged_players : list[PartialPlayer]
        A list of all merged profiles.\n
        Only ID and platform are present.
    created_at : datetime.datetime | None
        A timestamp of the profile's creation date.\n
        This can be `None` for accounts that are really old.
    last_login : datetime.datetime | None
        A timestamp of the profile's last successful in-game login.\n
        This can be `None` for accounts that are really old.
    platform_name : str
        The platform name of this profile. This is usually identical to `name`, except in cases
        where the platform allows nicknames (Steam profiles).
    title : str
        The player's currently equipped title.\n
        This will be an empty string without any title equipped.
    avatar_id : int
        The player's curremtly equipped avatar ID.
    avatar_url : str
        The player's currently equipped avatar URL.
    loading_frame : str
        The player's currently equipped loading frame name.\n
        This will be an empty string without any loading frame equipped.
    level : int
        The in-game level of this profile.
    playtime : Duration
        The amount of time spent playing on this profile.
    champion_count : int
        The amount of champions this player has unlocked.
    region : Region
        The player's currently set `Region`.\n
        This can be `Region.Unknown` for accounts that are really old.
    total_achievements : int
        The amount of achievements the player has.
    total_experience : int
        The total amount of experience the player has.
    casual : Stats
        Player's casual statistics.
    ranked_keyboard : RankedStats
        Player's ranked keyboard statistics.
    ranked_controller : RankedStats
        Player's ranked controller statistics.
    """
    def __init__(self, api: DataCache, player_data: responses.PlayerObject):
        # delay super() to pre-process player names
        player_name: str | None = player_data["hz_player_name"]
        gamer_tag: str | None = player_data["hz_gamer_tag"]
        name: str = player_data["Name"]
        self.platform_name: str = name
        if player_name is not None:
            name = player_name
        elif gamer_tag is not None:  # pragma: no branch
            name = gamer_tag
        super().__init__(
            api,
            id=player_data["Id"],
            name=name,
            platform=player_data["Platform"],
            # No private kwarg here, since this object can only exist for non-private accounts
        )
        self.active_player: PartialPlayer | None = None
        if player_data["ActivePlayerId"] != self._id:  # pragma: no cover
            self.active_player = PartialPlayer(api, id=player_data["ActivePlayerId"])
        self.merged_players: list[PartialPlayer] = []
        if player_data["MergedPlayers"] is not None:
            for p in player_data["MergedPlayers"]:
                self.merged_players.append(
                    PartialPlayer(api, id=p["playerId"], platform=p["portalId"])
                )
        self.created_at: datetime | None = None
        self.last_login: datetime | None = None
        if created_stamp := player_data["Created_Datetime"]:
            self.created_at = _convert_timestamp(created_stamp)
        if login_stamp := player_data["Last_Login_Datetime"]:
            self.last_login = _convert_timestamp(login_stamp)
        self.level: int = player_data["Level"]
        self.title: str = player_data["Title"] or ''
        self.avatar_id: int = player_data["AvatarId"]
        self.avatar_url: str = (
            player_data["AvatarURL"]
            or "https://hirez-api-docs.herokuapp.com/paladins/avatar/0"  # patch null here
        )
        self.loading_frame: str = player_data["LoadingFrame"] or ''
        self.playtime = Duration(minutes=player_data["MinutesPlayed"])
        self.champion_count: int = player_data["MasteryLevel"]
        self.region = Region(player_data["Region"], _return_default=True)
        self.total_achievements: int = player_data["Total_Achievements"]
        self.total_experience: int = player_data["Total_XP"]
        self.casual = Stats(player_data)
        self.ranked_keyboard = RankedStats("Keyboard", player_data["RankedKBM"])
        self.ranked_controller = RankedStats("Controller", player_data["RankedController"])

    @cached_property
    def ranked_best(self) -> RankedStats:
        """
        Player's best ranked statistics, between the keyboard and controller ones.

        If the rank is the same, winrate is used to determine the one returned.

        :type: RankedStats
        """
        if self.ranked_controller.rank == self.ranked_keyboard.rank:
            return max(self.ranked_keyboard, self.ranked_controller, key=lambda r: r.winrate)
        return max(self.ranked_keyboard, self.ranked_controller, key=lambda r: r.rank)

    @cached_property
    def calculated_level(self) -> int:
        """
        The calculated level of this profile.

        This uses `total_experience` to calculate the player's level, instead of relying on the
        value returned from the API.
        It also allows you to calculate the theorethical level beyond the 999th level cap.

        :type: int
        """
        # Players start at level 1, with 40k EXP needed to reach level 2. Afterwards,
        # the requirement grows by 20k per level, until reaching 1M EXP at level 50. After that,
        # each consecutive level consistently needs 1M EXP to advance.
        lvl_threshold = 25_480_000  # amount of EXP needed for lvl 50
        if self.total_experience < lvl_threshold:
            s = 0
            for lvl in range(2, 51):
                if (s := s + lvl * 20_000) > self.total_experience:
                    return lvl - 1
            return 50  # pragma: no cover  # failsafe, this will never run
        else:
            return ((self.total_experience - lvl_threshold) // 1_000_000) + 50
