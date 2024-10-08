from __future__ import annotations

import logging
from itertools import count
from typing import Iterable, Generator, TYPE_CHECKING

from .exceptions import NotFound
from .enums import Queue, Region, Rank
from .utils import chunk, _convert_map_name, _deduplicate
from .mixins import (
    CacheClient, CacheObject, MatchMixin, MatchPlayerMixin, Expandable, WinLoseMixin
)

if TYPE_CHECKING:
    from . import responses
    from .enums import Language
    from .champion import Champion, Skin
    from .cache import DataCache, CacheEntry
    from .player import PartialPlayer, Player


__all__ = [
    "PartialMatch",
    "MatchPlayer",
    "Match",
    "LivePlayer",
    "LiveMatch",
]
logger = logging.getLogger(__package__)


# this is a close duplicate of `PaladinsAPI.get_players`, modified for speed and its usage
async def _get_players(cache: DataCache, player_ids: Iterable[int]) -> dict[int, Player]:
    ids_list: list[int] = _deduplicate(player_ids, 0)  # also remove private accounts
    if not ids_list:  # pragma: no cover
        return {}
    from .player import Player  # cyclic import
    players_dict: dict[int, Player] = {}
    for chunk_ids in chunk(ids_list, 20):
        chunk_response = await cache.request("getplayerbatch", ','.join(map(str, chunk_ids)))
        for player_data in chunk_response:
            if player_data["ret_msg"]:  # pragma: no cover, skip private accounts
                continue
            player = Player(cache, player_data)
            players_dict[player.id] = player
    return players_dict


class PartialMatch(MatchPlayerMixin, MatchMixin, Expandable["Match"]):
    """
    Represents a match from a single player's perspective only.

    This partial object is returned by the `PartialPlayer.get_match_history` player's method.
    To obtain an object with all match information, try awaiting on this object like so:

    .. code-block:: py

        match = await partial_match

    Attributes
    ----------
    id : int
        The match ID.
    queue : Queue
        The queue this match was played in.
    region : Region
        The region this match was played in.
    timestamp : datetime.datetime
        A timestamp of when this match happened.
    duration : Duration
        The duration of the match.
    map_name : str
        The name of the map played.
    score : tuple[int, int]
        The match's ending score.\n
        The first value is always the allied-team score, while the second one - enemy team score.
    winning_team : Literal[1, 2]
        The winning team of this match.
    player : PartialPlayer | Player
        The player who participated in this match.\n
        This is usually a new partial player object.\n
        All attributes, Name, ID and Platform, should be present.
    champion : Champion | CacheObject
        The champion used by the player in this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    loadout : MatchLoadout
        The loadout used by the player in this match.
    items : list[MatchItem]
        A list of items bought by the player during this match.
    credits : int
        The amount of credits earned this match.
    experience : int
        The base amount of experience gained from this match.
    kills : int
        The amount of kills.
    deaths : int
        The amount of deaths.
    assists : int
        The amount of assists.
    damage_done : int
        The amount of damage dealt.
    damage_bot : int
        The amount of damage done by the player's bot after they disconnected.
    damage_taken : int
        The amount of damage taken.
    damage_mitigated : int
        The amount of damage mitigated (shielding).
    healing_done : int
        The amount of healing done to other players.
    healing_bot : int
        The amount of healing done by the player's bot after they disconnected.
    healing_self : int
        The amount of healing done to self (self-sustain).
    objective_time : int
        The amount of objective time the player got, in seconds.
    multikill_max : int
        The maximum multikill player did during the match.
    skin : Skin | CacheObject
        The skin the player had equipped for this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    team_number : Literal[1, 2]
        The team this player belongs to.
    team_score : int
        The score of the player's team.
    winner : bool
        `True` if the player won this match, `False` otherwise.
    """
    def __init__(
        self,
        player: PartialPlayer | Player,
        language: Language,
        cache_entry: CacheEntry,
        match_data: responses.HistoryMatchObject,
    ):
        MatchPlayerMixin.__init__(self, player, cache_entry, match_data)
        MatchMixin.__init__(self, match_data)
        self._language = language

    async def _expand(self) -> Match:
        """
        Upgrades this object into a full `Match` one, containing all match players and information.

        Uses up a single request.

        Returns
        -------
        Match
            The full match object.

        Raises
        ------
        NotFound
            The match could not be found.
        """
        logger.info(f"PartialMatch(id={self.id}).expand()")
        response = await self._api.request("getmatchdetails", self.id)
        if not response:
            raise NotFound("Match")
        cache_entry = await self._api._fetch_entry(self._language)
        return Match(self._api, cache_entry, response, {})

    def __repr__(self) -> str:
        return f"{self.queue.name}: {self.champion.name}: {self.kda_text}"

    @property
    def disconnected(self) -> bool:
        """
        Returns `True` if the player has disconnected during the match, `False` otherwise.\n
        This is done by checking if either `damage_bot` or `healing_bot` are non zero.

        :type: bool
        """
        return self.damage_bot > 0 or self.healing_bot > 0


class MatchPlayer(MatchPlayerMixin):
    """
    Represents a full match's player.

    Attributes
    ----------
    match : Match
        The match this player belongs to.
    player : PartialPlayer | Player
        The player itself who participated in this match.\n
        This is usually a new partial player object.\n
        All attributes, Name, ID and Platform, should be present.
    rank : Rank | None
        The player's rank.

        .. warning::

            Due to API limitations, this is only available for matches played in ranked queues.\n
            For other queues, this attribute will be `None`.
    champion : Champion | CacheObject
        The champion used by the player in this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    loadout : MatchLoadout
        The loadout used by the player in this match.
    items : list[MatchItem]
        A list of items bought by the player during this match.
    credits : int
        The amount of credits earned this match.
    experience : int
        The base amount of experience gained from this match.
    kills : int
        The amount of player kills.
    deaths : int
        The amount of deaths.
    assists : int
        The amount of assists.
    damage_done : int
        The amount of damage dealt.
    damage_bot : int
        The amount of damage done by the player's bot after they disconnected.
    damage_taken : int
        The amount of damage taken.
    damage_mitigated : int
        The amount of damage mitigated (shielding).
    healing_done : int
        The amount of healing done to other players.
    healing_bot : int
        The amount of healing done by the player's bot after they disconnected.
    healing_self : int
        The amount of healing done to self (self-sustain).
    objective_time : int
        The amount of objective time the player got, in seconds.
    multikill_max : int
        The maximum multikill player did during the match.
    skin : Skin | CacheObject
        The skin the player had equipped for this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    team_number : Literal[1, 2]
        The team this player belongs to.
    team_score : int
        The score of the player's team.
    winner : bool
        `True` if the player won this match, `False` otherwise.
    points_captured : int
        The amount of times the player's team captured the point.\n
        This is ``0`` for non-Siege matches.
    push_successes : int
        The amount of times the player's team successfully pushed the payload to the end.\n
        This is ``0`` for non-Siege matches.
    kills_bot : int
        The amount of bot kills.
    account_level : int
        The player's account level.
    mastery_level : int
        The player's champion mastery level.
    party_number : int
        A number denoting the party the player belonged to.\n
        ``0`` means the player wasn't in a party.
    """
    def __init__(
        self,
        match: Match,
        cache_entry: CacheEntry,
        player_data: responses.MatchPlayerObject,
        parties: dict[int, int],
        players: dict[int, Player],
    ):
        player: PartialPlayer | Player | None = players.get(int(player_data["playerId"]))
        if player is None:
            # if no full player was found
            from .player import PartialPlayer  # cyclic imports
            player = PartialPlayer(
                match._api,
                id=player_data["playerId"],
                name=player_data["playerName"],
                platform=player_data["playerPortalId"],
            )
        super().__init__(player, cache_entry, player_data)
        self.rank: Rank | None
        if match.queue.is_ranked():
            self.rank = Rank(player_data["League_Tier"], _return_default=True)
        else:
            self.rank = None
        self.points_captured: int = player_data["Kills_Gold_Fury"]
        self.push_successes: int = player_data["Kills_Fire_Giant"]
        self.kills_bot: int  = player_data["Kills_Bot"]
        self.account_level: int = player_data["Account_Level"]
        self.mastery_level: int = player_data["Mastery_Level"]
        self.party_number: int = parties.get(player_data["PartyId"], 0)
        self.killing_spree: int = player_data["Killing_Spree"]

    @property
    def disconnected(self) -> bool:
        """
        Returns `True` if the player has disconnected during the match, `False` otherwise.\n
        This is done by checking if either `damage_bot` or `healing_bot` are non zero.

        :type: bool
        """
        return self.damage_bot > 0 or self.healing_bot > 0

    def __repr__(self) -> str:
        return (
            f"{self.player.name or 'Unknown'}({self.player.id}): {self.champion.name}: "
            f"({self.kda_text}, {self.damage_done}, {self.healing_done})"
        )


class Match(CacheClient, MatchMixin):
    """
    Represents already-played full match information.

    You can get this from the `PaladinsAPI.get_match` and `PaladinsAPI.get_matches` methods,
    as well as from upgrading a `PartialMatch` object.

    Attributes
    ----------
    id : int
        The match ID.
    queue : Queue
        The queue this match was played in.
    region : Region
        The region this match was played in.
    timestamp : datetime.datetime
        A timestamp of when this match happened.
    duration : Duration
        The duration of the match.
    map_name : str
        The name of the map played.
    score : Tuple[int, int]
        The match's ending score.\n
        The first value is the ``team1`` score, while the second value - ``team2`` score.
    winning_team : Literal[1, 2]
        The winning team of this match.
    replay_available : bool
        `True` if this match has a replay that you can watch, `False` otherwise.
    bans : list[Champion | CacheObject | None]
        A list of champions banned in this match.\n
        With incomplete cache, the list will contain `CacheObject` objects
        with the name and ID set.\n
        This will be an empty list for non-ranked matches.\n
        `None` indicates there was no ban.
    team1 : list[MatchPlayer]
        A list of players in the first team.
    team2 : list[MatchPlayer]
        A list of players in the second team.
    players : Generator[MatchPlayer]
        A generator that iterates over all match players in the match.
    """
    def __init__(
        self,
        api: DataCache,
        cache_entry: CacheEntry,
        match_data: list[responses.MatchPlayerObject],
        players: dict[int, Player],
    ):
        CacheClient.__init__(self, api)
        first_player = match_data[0]
        MatchMixin.__init__(self, first_player)
        logger.debug(f"Match(id={self.id}) -> creating...")
        self.replay_available: bool = first_player["hasReplay"] == "y"
        self.bans: list[Champion | CacheObject | None] = []
        if self.queue.is_ranked():
            i = 0
            while True:
                i += 1
                if (ban_key := f"BanId{i}") not in first_player:
                    break
                ban_id: int = first_player[ban_key]  # type: ignore[literal-required]
                if not ban_id:  # pragma: no cover
                    # zero indicates no ban has happened - use None
                    self.bans.append(None)
                    continue
                ban_champ: Champion | CacheObject = cache_entry.champions._cache_object(
                    ban_id,
                    first_player.get(f"Ban_{i}", ''),  # type: ignore[arg-type]
                )
                self.bans.append(ban_champ)
        self.team1: list[MatchPlayer] = []
        self.team2: list[MatchPlayer] = []
        # Determine party numbers
        # We need to do this here because apparently one-man parties are a thing
        party_count = count(1)
        parties: dict[int, int] = {}
        for player_data in match_data:
            pid = player_data["PartyId"]
            # process only non-0 parties
            if pid:
                if pid not in parties:
                    # haven't seen this one yet, assign zero
                    parties[pid] = 0
                elif parties[pid] == 0:
                    # we've seen this one, and it doesn't have a number assigned - assign one
                    parties[pid] = next(party_count)
        # iterate over a second time, now that we have the party numbers sorted out
        for player_data in match_data:
            match_player = MatchPlayer(self, cache_entry, player_data, parties, players)
            team_number = player_data["TaskForce"]
            if team_number == 1:
                self.team1.append(match_player)
            elif team_number == 2:  # pragma: no branch
                self.team2.append(match_player)
        logger.debug(f"Match(id={self.id}) -> created")

    @property
    def players(self) -> Generator[MatchPlayer, None, None]:
        for p in self.team1:
            yield p
        for p in self.team2:
            yield p

    def __repr__(self) -> str:
        return f"{self.queue.name}({self.id}): {self.score}"

    async def expand_players(self):
        """
        Makes partial player objects in the containing match player objects be expanded into
        full `Player` objects, if possible.

        Uses up a single request to do the expansion.
        """
        players_dict = await _get_players(self._api, (p.player.id for p in self.players))
        for mp in self.players:
            pid = mp.player.id
            # skip 0s
            if pid == 0:  # pragma: no cover
                continue
            if (p := players_dict.get(pid)) is not None:  # pragma: no branch
                mp.player = p


class LivePlayer(WinLoseMixin, CacheClient):
    """
    Represents a live match player.
    You can find these on the `LiveMatch.team1` and `LiveMatch.team2` attributes.

    Attributes
    ----------
    match: LiveMatch
        The match this player belongs to.
    player : PartialPlayer | Player
        The actual player playing in this match.
    champion : Champion | CacheObject
        The champion the player is using in this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    skin : Skin | CacheObject
        The skin the player has equipped for this match.\n
        With incomplete cache, this will be a `CacheObject` with the name and ID set.
    rank : Rank | None
        The player's rank.

        .. warning::

            Due to API limitations, this is only available for matches played in ranked queues.\n
            For other queues, this attribute will be `None`.
    account_level : int
        The player's account level.
    mastery_level : int
        The player's champion mastery level.
    wins : int
        The amount of wins.
    losses : int
        The amount of losses.
    """
    def __init__(
        self,
        match: LiveMatch,
        cache_entry: CacheEntry,
        player_data: responses.LivePlayerObject,
        players: dict[int, Player],
    ):
        CacheClient.__init__(self, match._api)
        WinLoseMixin.__init__(
            self,
            wins=player_data["tierWins"],
            losses=player_data["tierLosses"],
        )
        self.match: LiveMatch = match
        # Player
        player: PartialPlayer | Player | None = players.get(int(player_data["playerId"]))
        if player is None:
            # if no full player was found
            from .player import PartialPlayer  # cyclic imports
            player = PartialPlayer(
                self._api,
                id=player_data["playerId"],
                name=player_data["playerName"],
                platform=player_data["playerPortalId"],
            )
        self.player: PartialPlayer | Player = player
        # Champion and Skin
        self.champion: Champion | CacheObject = cache_entry.champions._cache_object(
            player_data["ChampionId"], player_data["ChampionName"]
        )
        self.skin: Skin | CacheObject = cache_entry.skins._cache_object(
            player_data["SkinId"], player_data["Skin"]
        )
        # Other
        self.rank: Rank | None
        if match.queue.is_ranked():  # pragma: no cover
            self.rank = Rank(player_data["Tier"], _return_default=True)
        else:
            self.rank = None
        self.account_level: int = player_data["Account_Level"]
        self.mastery_level: int = player_data["Mastery_Level"]

    def __repr__(self) -> str:
        return (
            f"{self.player.name or 'Unknown'}({self.player.id}): "
            f"{self.account_level} level: "
            f"{self.champion.name}({self.mastery_level})"
        )


class LiveMatch(CacheClient):
    """
    Represents an on-going live match.
    You can get this from the `PlayerStatus.get_live_match` method.

    Attributes
    ----------
    id : int
        The match ID.
    map_name : str
        The name of the map played.
    queue : Queue
        The queue the match is being played in.
    region : Region
        The region this match is being played in.
    team1 : list[LivePlayer]
        A list of live players in the first team.
    team2 : list[LivePlayer]
        A list of live players in the second team.
    players : Generator[LivePlayer]
        A generator that iterates over all live match players in the match.
    """
    def __init__(
        self,
        api: DataCache,
        cache_entry: CacheEntry,
        match_data: list[responses.LivePlayerObject],
        players: dict[int, Player],
    ):
        super().__init__(api)
        first_player = match_data[0]
        self.id: int = first_player["Match"]
        self.map_name: str = _convert_map_name(first_player["mapGame"])
        self.queue = Queue(int(first_player["Queue"]), _return_default=True)
        self.region = Region(first_player["playerRegion"], _return_default=True)
        self.team1: list[LivePlayer] = []
        self.team2: list[LivePlayer] = []
        for player_data in match_data:
            live_player = LivePlayer(self, cache_entry, player_data, players)
            if player_data["taskForce"] == 1:
                self.team1.append(live_player)
            elif player_data["taskForce"] == 2:  # pragma: no branch
                self.team2.append(live_player)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.queue.name}): {self.map_name}"

    @property
    def players(self) -> Generator[LivePlayer, None, None]:
        for p in self.team1:
            yield p
        for p in self.team2:
            yield p

    async def expand_players(self):
        """
        Makes partial player objects in the containing match player objects be expanded into
        full `Player` objects, if possible.

        Uses up a single request to do the expansion.
        """
        players_dict = await _get_players(self._api, (p.player.id for p in self.players))
        for mp in self.players:
            pid = mp.player.id
            # skip 0s
            if pid == 0:  # pragma: no cover
                continue
            if (p := players_dict.get(pid)) is not None:  # pragma: no branch
                mp.player = p
