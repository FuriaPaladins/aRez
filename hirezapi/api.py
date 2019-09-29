import asyncio
from datetime import datetime, timedelta
from typing import Union, Optional, List, Dict

from .match import Match
from .items import Device
from .endpoint import Endpoint
from .champion import Champion
from .status import ServerStatus
from .player import Player, PartialPlayer
from .cache import DataCache, ChampionInfo
from .utils import get, chunk, convert_timestamp
from .enumerations import Language, Platform, Queue

class PaladinsAPI:
    """
    The main Paladins API.

    Parameters
    ----------
    dev_id : Union[int, str]
        Your developer's ID (devId).
    auth_key : str
        Your developer's authentication key (authKey).
    loop : Optional[asyncio.AbstractEventLoop]
        The loop you want to use for this API.\n
        Default loop is used when not provided.
    """
    def __init__(self, dev_id: Union[int, str], auth_key: str, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        if loop is None:
            loop = asyncio.get_event_loop()
        # don't store the endpoint - the API should have no access to it's instance other than the request and close methods
        endpoint = Endpoint("http://api.paladins.com/paladinsapi.svc", dev_id, auth_key, loop=loop)
        self._server_status = None
        self._cache = DataCache()
        # forward endpoint request and close methods
        self.request = endpoint.request
        self.close = endpoint.close
        # forward cache get methods
        self.get_champion = self._cache.get_champion
        self.get_card     = self._cache.get_card
        self.get_talent   = self._cache.get_talent
        self.get_item     = self._cache.get_item
    
    # async with integration
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()
    
    async def get_server_status(self, force_refresh: bool = False) -> Optional[ServerStatus]:
        """
        Fetches the server status.

        To preserve requests, the status returned is cached once every minute.
        Use the ``force_refresh`` parameter to override this behavior.

        Uses up one request each time the cache is refreshed.
        
        Parameters
        ----------
        force_refresh : Optional[bool]
            Bypasses the cache, forcing a fetch and returning a new object.\n
            Defaults to `False`.
        
        Returns
        -------
        Optional[ServerStatus]
            The server status object.\n
            `None` is returned if there is no cached status and fetching returned an empty response.
        """
        if self._server_status is None or datetime.utcnow() - timedelta(minutes=1) >= self._server_status.timestamp or force_refresh:
            response = await self.request("gethirezserverstatus")
            if response:
                self._server_status = ServerStatus(response)
        
        return self._server_status
    
    async def get_champion_info(self, language: Language = Language.English, force_refresh: bool = False) -> Optional[ChampionInfo]:
        """
        Fetches the champion information.

        To preserve requests, the information returned is cached once every 12 hours.
        Use the ``force_refresh`` parameter to override this behavior.

        Uses up two requests each time the cache is refreshed, per language.
        
        Parameters
        ----------
        language : Optional[Language]
            The Language you want to fetch the information in.\n
            Defaults to `Language.English`
        force_refresh : Optional[bool]
            Bypasses the cache, forcing a fetch and returning a new object.\n
            Defaults to `False`.
        
        Returns
        -------
        Optional[ChampionInfo]
            An object containing all champions, cards, talents and items information in the chosen language.\n
            `None` is returned if there was no cached information and fetching returned an empty response.
        """
        assert isinstance(language, Language)

        if self._cache._needs_refreshing(language) or force_refresh:
            champions_response = await self.request("getgods", [language.value])
            items_response = await self.request("getitems", [language.value])
            if champions_response and items_response:
                self._cache[language] = (champions_response, items_response)

        return self._cache[language] # DataCache uses `.get()` on the internal dict, so this won't cause a KeyError

    def get_player_from_id(self, player_id: int) -> PartialPlayer:
        """
        Wraps a player ID into a `PartialPlayer` object.

        Note that since there is no input validation, so there's no guarantee an object created this way
        will return any meaningful results when it's methods are used.
        
        Parameters
        ----------
        player_id : int
            The Player ID you want to get object for.
        
        Returns
        -------
        PartialPlayer
            An object with only the ID set.
        """
        assert isinstance(player_id, int)
        return PartialPlayer(self, {"player_id": player_id})
    
    async def get_player(self, player: Union[int, str]) -> Optional[Player]:
        """
        Fetches a Player object for the given player ID or player name.

        Only players with `Platform.Steam`, `Platform.HiRez` and `Platform.Discord`
        platforms will be returned when using this method with player name as input.
        For player ID inputs, players from all platforms will be returned.

        Uses up a single request.
        
        Parameters
        ----------
        player : Union[int, str]
            Player ID or player name of the player you want to get object for.
        
        Returns
        -------
        Optional[Player]
            An object containing basic information about the player requested.\n
            `None` is returned if a Player for the given ID or Name could not be found.
        """
        assert isinstance(player, (int, str))
        player_list = await self.request("getplayer", [player])
        if player_list:
            return Player(self, player_list[0])
    
    async def search_players(self, player_name: str, platform: Platform = None) -> List[PartialPlayer]:
        """
        Fetches all players whose name matches the name specified.

        The search is fuzzy - player name capitalisation doesn't matter.

        Uses up a single request.
        
        Parameters
        ----------
        player_name : str
            Player name you want to search for.
        platform : Optional[Platform]
            Platform you want to limit the search to.\n
            Specifying `None` will search on all platforms.\n
            Defaults to `None`.
        
        Returns
        -------
        List[PartialPlayer]
            A list of partial players whose name matches the specified name.
        """
        assert isinstance(player_name, str)
        assert isinstance(platform, (None.__class__, Platform))
        player_name = player_name.lower()
        if platform:
            if platform.value <= 5 or platform.value == 25: # hirez, pc, steam and discord only
                list_response = await self.request("getplayeridbyname", [player_name])
            else:
                list_response = await self.request("getplayeridsbygamertag", [platform.value, player_name])
        else:
            response = await self.request("searchplayers", [player_name])
            list_response = [r for r in response if r["Name"].lower() == player_name]
        return [PartialPlayer(self, p) for p in list_response]
    
    async def get_from_platform(self, platform_id: int, platform: Platform) -> Optional[PartialPlayer]:
        """
        Fetches a PartialPlayer linked with the platform ID specified.

        Uses up a single request.
        
        Parameters
        ----------
        platform_id : int
            The platform-specific ID of the linked player.\n
            This is usually SteamID64, Discord User ID, etc.
        platform : Platform
            The platform this ID is for.
        
        Returns
        -------
        Optional[PartialPlayer]
            The player this platform ID is linked to.\n
            `None` is returned if the player couldn't be found.
        """
        assert isinstance(platform_id, int)
        assert isinstance(platform, Platform)
        response = await self.request("getplayeridbyportaluserid", [platform.value, platform_id])
        if response:
            return PartialPlayer(self, response[0])
    
    async def get_match(self, match_id: int, language: Language = Language.English) -> Optional[Match]:
        """
        Fetches a match for the given Match ID.

        Uses up a single request.
        
        Parameters
        ----------
        match_id : int
            Match ID you want to get a match for.
        language : Optional[Language]
            The Language you want to fetch the information in.\n
            Defaults to `Language.English`
        
        Returns
        -------
        Optional[Match]
            A match for the ID specified.\n
            `None` is returned if the match wasn't available on the server.
        """
        assert isinstance(match_id, int)
        assert isinstance(language, Language)
        # ensure we have champion information first
        await self.get_champion_info(language)
        response = await self.request("getmatchdetails", [match_id])
        if response:
            return Match(self, language, response)

    async def get_matches(self, match_ids: List[int], language: Language = Language.English) -> List[Match]:
        """
        Fetches multiple matches in a batch, for the given Match IDs.

        Uses up a single request for every multiple of 10 match IDs passed.
        
        Parameters
        ----------
        match_ids : List[int]
            The list of Match IDs you want to fetch.
        language : Optional[Language]
            The ``Language`` you want to fetch the information in.\n
            Defaults to `Language.English`
        
        Returns
        -------
        List[Match]
            Returns a list of the available matches requested.\n
            Some of the matches can be not present if they weren't available on the server.
        """
        assert isinstance(match_ids, list)
        assert all(isinstance(mid, int) for mid in match_ids)
        assert isinstance(language, Language)
        if not match_ids:
            return []
        # ensure we have champion information first
        await self.get_champion_info(language)
        matches = []
        for chunk_ids in chunk(match_ids, 10): # chunk the IDs into groups of 10
            response = await self.request("getmatchdetailsbatch", [','.join(map(str, chunk_ids))])
            chunk_matches = {}
            for p in response:
                chunk_matches.setdefault(p["Match"], []).append(p)
            chunk_matches = [Match(self, language, match_list) for match_list in chunk_matches.values()]
            chunk_matches.sort(key=lambda m: chunk_ids.index(m.id))
            matches.extend(chunk_matches)
        return matches

    async def get_matches_for_queue(
        self,
        queue: Queue,
        *,
        start: datetime,
        end: datetime,
        reverse: bool = False
    ):
        assert isinstance(queue, Queue)
        assert isinstance(start, datetime)
        assert isinstance(end, (None.__class__, datetime))
        assert isinstance(reverse, bool)
