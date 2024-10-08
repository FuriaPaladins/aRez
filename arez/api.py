from __future__ import annotations

import re
import aiohttp
import asyncio
import logging
from operator import itemgetter
from datetime import timedelta, timezone
import datetime
from inspect import Parameter, signature, iscoroutinefunction
from typing import Any, Literal, NoReturn, cast, overload, TYPE_CHECKING
from collections.abc import Coroutine, Iterable, Sequence, Callable, AsyncGenerator

from . import responses
from .stats import DataUsed
from .cache import DataCache
from .bounty import BountyItem
from .status import ServerStatus
from .statuspage import StatusPage
from .match import Match, _get_players
from .player import Player, PartialPlayer
from .enums import Language, Platform, Queue, Region, PC_PLATFORMS
from .utils import chunk, group_by, _date_gen, _convert_timestamp, _deduplicate
from .exceptions import HTTPException, Private, NotFound, Unavailable, LimitReached

if TYPE_CHECKING:
    from .cache import CacheEntry
    from .statuspage import ComponentGroup, CurrentStatus


__all__ = ["PaladinsAPI"]
logger = logging.getLogger(__package__)
_CHECK_INTERVAL = timedelta(minutes=3)
_RECHECK_INTERVAL = timedelta(minutes=1)


class PaladinsAPI(DataCache):
    """
    The main Paladins API.

    Inherits from `DataCache`.

    .. note::

        You can request your developer ID and authorization key `here.
        <https://fs12.formsite.com/HiRez/form48/secure_index.html>`_


    .. note::

        In addition to the exceptions specified for each method, each API request can result
        in the following exceptions being raised:

        `Unavailable`
            The API is currently unavailable.
        `LimitReached`
            Your daily limit of requests has been reached.
        `HTTPException`
            Fetching the information requested failed due to connection or API problems.

    Parameters
    ----------
    dev_id : int | str
        Your developer's ID (devId).
    auth_key : str
        Your developer's authentication key (authKey).
    cache : bool
        When set to `False`, this disables the data cache. This makes most objects returned
        from the API be `CacheObject` instead of their respective data-rich counterparts.\n
        Defaults to `True`.
    initialize : bool | Language
        When set to `True`, it launches a task that will initialize the cache with
        the default (English) language.\n
        Can be set to a `Language` instance, in which case that language will be set as default
        first, before initializing.\n
        Defaults to `False`, where no initialization occurs.
    loop : asyncio.AbstractEventLoop | None
        The event loop you want to use for this API.\n
        Default loop is used when not provided.
    """
    def __init__(
        self,
        dev_id: int | str,
        auth_key: str,
        *,
        cache: bool = True,
        initialize: bool | Language = False,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        if loop is None:  # pragma: no branch
            loop = asyncio.get_event_loop()
        super().__init__(
            "https://api.paladins.com/paladinsapi.svc",
            dev_id,
            auth_key,
            loop=loop,
            enabled=cache,
            initialize=initialize,
        )
        self._statuspage = StatusPage("http://status.hirezstudios.com", loop=loop)
        self._statuspage_group = "Paladins"
        self._server_status: ServerStatus | None = None
        self._status_callback: (
            Callable[[ServerStatus, ServerStatus], Coroutine[Any, Any, Any]] | None
        ) = None
        self._status_task: asyncio.Task[NoReturn] | None = None
        self._status_intervals: tuple[timedelta, timedelta] = (
            _CHECK_INTERVAL, _RECHECK_INTERVAL  # check, recheck
        )

    # solely for typing, __aexit__ exists in the Endpoint
    async def __aenter__(self) -> PaladinsAPI:
        return cast(PaladinsAPI, await super().__aenter__())

    async def close(self):
        """
        Closes the underlying API connection,
        and stops the server status checking loop (see `register_status_callback`).

        Attempting to make a request after the connection is closed
        will result in a `RuntimeError`.
        """
        if self._status_task is not None:  # pragma: no cover
            self._status_task.cancel()
        await asyncio.gather(super().close(), self._statuspage.close())

    async def get_data_used(self) -> DataUsed:
        """
        Allows you to check API usage statistics.

        Returns
        -------
        DataUsed
            An object describing API usage.
        """
        response = await self.request("getdataused")
        return DataUsed(response[0])

    async def get_server_status(self, *, force_refresh: bool = False) -> ServerStatus:
        """
        Fetches the server status.

        To preserve requests, the status returned is cached once every minute.
        Use the ``force_refresh`` parameter to override this behavior.

        Uses up one request each time the cache is refreshed.

        Parameters
        ----------
        force_refresh : bool
            Bypasses the cache, forcing a fetch and returning a new object.\n
            Defaults to `False`.

        Returns
        -------
        ServerStatus
            The server status object.

        Raises
        ------
        NotFound
            There was no cached status and fetching has failed.
        """
        # Use a lock to ensure we're not fetching this twice in quick succession
        async with self._locks["server_status"]:
            if (
                not force_refresh
                and self._server_status is not None
                and datetime.datetime.now(datetime.UTC) < self._server_status.timestamp + timedelta(minutes=1)
            ):
                # it hasn't been 1 minute since the last fetch - use cached
                logger.info(f"api.get_server_status({force_refresh=}) -> using cached")
                return self._server_status
            logger.info(f"api.get_server_status({force_refresh=}) -> fetching new")
            # fetch from the official API
            try:
                api_status = await self.request("gethirezserverstatus")
            except (HTTPException, Unavailable, LimitReached):  # pragma: no cover
                api_status = []  # no data could be fetched
            if api_status and api_status[0]["ret_msg"]:  # pragma: no cover
                # got an error from official API - use empty
                api_status = []
            # unify PTS as a platform, not an environment
            if pts_dict := next(  # pragma: no branch
                (s for s in api_status if s["environment"] == "pts"), None
            ):
                pts_dict["platform"] = pts_dict["environment"]  # type: ignore[typeddict-item]

            # fetch from the StatusPage
            group: ComponentGroup | None
            try:
                page_status: CurrentStatus = await self._statuspage.get_status()
            except (
                asyncio.TimeoutError,
                aiohttp.ClientResponseError,
                aiohttp.ClientConnectionError,
            ):
                group = None  # no data could be fetched
            else:
                group = page_status.group(self._statuspage_group)

            if not api_status and group is None:
                # can't do anything here chief - use cached, if possible
                if self._server_status is None:
                    logger.info(f"api.get_server_status({force_refresh=}) -> fetching failed")
                    raise NotFound("Server status")
                logger.info(
                    f"api.get_server_status({force_refresh=}) -> fetching failed, using cached"
                )
                return self._server_status

            # pack it and cache
            logger.info(f"api.get_server_status({force_refresh=}) -> fetching successful")
            self._server_status = ServerStatus(api_status, group)
        return self._server_status

    async def _status_loop(self):
        while True:
            delay_delta = self._status_intervals[0]  # normal check interval
            try:
                old_status = self._server_status
                new_status = await self.get_server_status(force_refresh=True)
                if (
                    old_status is not None
                    and old_status != new_status
                    and self._status_callback is not None
                ):
                    self._loop.create_task(self._status_callback(old_status, new_status))
            except NotFound:  # pragma: no cover
                # just skip it this time, use recheck interval
                delay_delta = self._status_intervals[1]
            except Exception:  # pragma: no cover
                # unknown exception, use recheck interval
                logger.exception("Exception in the server status loop")
                delay_delta = self._status_intervals[1]
            else:
                if not new_status.all_up or new_status.limited_access:
                    # there is trouble, use recheck interval
                    delay_delta = self._status_intervals[1]
            await asyncio.sleep(delay_delta.total_seconds())

    def register_status_callback(
        self,
        callback: (
            None | Callable[[ServerStatus], Any] | Callable[[ServerStatus, ServerStatus], Any]
        ),
        check_interval: timedelta = _CHECK_INTERVAL,
        recheck_interval: timedelta = _RECHECK_INTERVAL,
    ):
        """
        Registers a callback function, that will periodically check the status of the servers,
        every ``check_interval`` specified. If the status changes, the callback function
        will then be called with the new status (and optionally the previous one) passed as the
        arguments, like so: ``callback(after)`` or ``callback(before, after)``.

        This can be used to easily setup server status monitoring and notifications systems:

        .. code-block:: py

            api: arez.PaladinsAPI  # wrapper instance

            async def notify(after: arez.ServerStatus):
                # do anything when the status changes - in this case, just print a message
                print(f"Current server status: {after.status}")

            api.register_status_callback(notify)

        .. note::

            - The checking loop will call `get_server_status` with ``force_refresh`` \
            set to `True`, to handle the refreshing. To avoid wasting request, \
            it's recommended to use intervals at least 1 minute long, \
            and not set ``force_refresh`` to `True` anywhere else in your code, \
            relying on the cached status being refreshed by the loop.

            - The callback is wrapped inside a task, to prevent it from \
            delaying the checking loop. Please make sure that it's execution time \
            is shorter than the check / recheck intervals, \
            otherwise you may end up with two callbacks running at once.

            - Any exceptions raised by the callback function, will be logged by the library's \
            logger, and ultimately ignored otherwise. If you'd be interested in those, \
            try either catching and processing those yourself (within the callback), \
            or hooking up a logger handler and setting the logger to at least ``ERROR`` level.

        Parameters
        ----------
        callback : Callable[[ServerStatus], Any] | \
        Callable[[ServerStatus, ServerStatus], Any]] | None
            The callback function you want to register. This can be either a normal function
            or an async one, accepting either ``1`` or ``2`` positional arguments,
            with any return type.\n
            Passing a new callback function, while one is already running,
            will overwrite the previous one and reset the timer.\n
            Passing `None` removes the callback and stops the checking loop.
        check_interval : timedelta
            The length of the interval used between Operational
            (all servers up, no limited access, not including PTS) server status checks.\n
            The default check interval is 3 minutes.
        recheck_interval : timedelta
            The length of the interval used between non-Operational
            (at least one server is down, or limited access, not including PTS)
            server status checks.\n
            The default recheck interval is 1 minute.

        Raises
        ------
        TypeError
            The callback passed was not a function.
        ValueError
            The callback passed had an incorrect number (or types) of its parameters.
        """
        if callback is None:
            if self._status_task is not None:
                self._status_task.cancel()
                self._status_task = None
            return
        if not callable(callback):
            raise TypeError("Callback has to be either a normal or async function")
        sig = signature(callback)
        arg_types = (Parameter.POSITIONAL_OR_KEYWORD, Parameter.POSITIONAL_ONLY)
        if not (
            1 <= len(sig.parameters) <= 2  # 1 or 2 args
            and all(arg.kind in arg_types for arg in sig.parameters.values())  # positionals only
        ):
            raise ValueError(
                "The callaback function has to accept either 1 or 2 positional arguments"
            )
        pass_before = len(sig.parameters) != 1
        is_coro = iscoroutinefunction(callback)

        async def _status_callback(before: ServerStatus, after: ServerStatus):
            try:
                args = (before, after) if pass_before else (after,)
                ret = callback(*args)  # type: ignore
                if is_coro:
                    await ret
            except Exception:
                logger.exception("Exception in the server status callback")
                raise  # raise up to the wrapping task

        if self._status_task is not None:
            self._status_task.cancel()
        self._status_callback = _status_callback
        self._status_intervals = (check_interval, recheck_interval)
        self._status_task = self._loop.create_task(self._status_loop())

    async def get_champion_info(
        self,
        language: Language | None = None,
        *,
        force_refresh: bool = False,
        cache: bool | None = None,
    ) -> CacheEntry:
        """
        Fetches the champions, talents, cards, shop items and skins information.

        To preserve requests, the information returned is cached once every 12 hours.
        Use the ``force_refresh`` parameter to override this behavior.

        Uses up three requests each time the cache is refreshed, per language.

        Parameters
        ----------
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.
        force_refresh : bool
            Bypasses the cache, forcing a fetch and returning a new object.\n
            Defaults to `False`.
        cache : bool | None
            Lets you decide if the received information should be cached or not.\n
            Setting this to `True` forces the object to be cached, even
            when the cache is disabled.\n
            Setting this to `False` will never cache the object.\n
            Default behavior (`None`) follows the cache existence setting.

        Returns
        -------
        CacheEntry
            An object containing all champions, cards, talents, shop items and skins information,
            in the chosen language.

        Raises
        ------
        NotFound
            The champion information wasn't available on the server.
        """
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._default_language
        logger.info(f"api.get_champion_info(language={language.name}, {force_refresh=})")
        entry = await self._fetch_entry(language, force_refresh=force_refresh, cache=cache)
        if entry is None:
            raise NotFound("Champion information")
        return entry

    def wrap_player(
        self,
        player_id: int,
        player_name: str = '',
        platform: str | int = 0,
        private: bool = False,
    ) -> PartialPlayer:
        """
        Wraps player ID, Name and Platform into a `PartialPlayer` object.

        .. warning::

            Note that since there is no input validation, there's no guarantee an object created
            this way will return any meaningful results when it's methods are used. This method
            is here purely for those who'd like to store player objects in something like
            a database, offering the possibility of re-wrapping the stored data back into
            a valid player object.

            This **should not** be used to fetch stats for players based on their ID - use
            `get_player` for this purpose instead.

        Parameters
        ----------
        player_id : int
            The player ID you want to get the object for.
        player_name : str
            The player Name you want the object to have.\n
            Defaults to an empty string.
        platform : str | int
            The platform you want the object to have.\n
            Defaults to `Platform.Unknown`.
        private : bool
            A boolean flag indicating if this profile should be considered private or not.\n
            Defaults to `False`.

        Returns
        -------
        PartialPlayer
            The wrapped player object.
        """
        logger.debug(f"api.wrap_player({player_id=}, {player_name=}, {platform=}, {private=})")
        return PartialPlayer(
            self, id=player_id, name=player_name, platform=platform, private=private
        )

    @overload
    async def get_player(
        self, player: int | str, *, return_private: Literal[False] = False
    ) -> Player:
        ...

    @overload
    async def get_player(
        self, player: int | str, *, return_private: Literal[True]
    ) -> Player | PartialPlayer:
        ...

    async def get_player(
        self, player: int | str, *, return_private: bool = False
    ) -> Player | PartialPlayer:
        """
        Fetches a Player object for the given player ID or player name.

        Only players with `Platform.PC`, `Platform.Steam` and `Platform.Discord`
        platforms (PC players) will be returned when using this method with player name
        as input. For player ID inputs, players from all platforms will be returned.

        Uses up a single request.

        Parameters
        ----------
        player : int | str
            Player ID or player name of the player you want to get object for.
        return_private : bool
            When set to `True` and the requested profile is determined private, this method will
            return a `PartialPlayer` object with the player ID and privacy flag set.\n
            When set to `False`, the `Private` exception will be raised instead.\n
            Defaults to `False`.

        Returns
        -------
        Player | PartialPlayer
            An object containing stats information about the player requested.\n
            `PartialPlayer` objects are only returned for private profiles and appropriate
            arguments used.

        Raises
        ------
        NotFound
            The player's profile doesn't exist / couldn't be found.
        Private
            The player's profile was private.
        """
        if not isinstance(player, (int, str)):
            raise TypeError(f"player argument has to be of int or str type, got {type(player)}")
        player = str(player)  # explicit cast to str
        # save on the request by raising Notfound for zero straight away
        if player == '0':
            raise NotFound("Player")
        logger.info(f"api.get_player({player=}, {return_private=})")
        player_list = await self.request("getplayer", player)
        if not player_list:
            # No one got returned
            raise NotFound("Player")
        player_data = player_list[0]
        # Check to see if their profile is private by chance
        ret_msg = player_data["ret_msg"]
        if ret_msg:
            # 'Player Privacy Flag set for:
            # playerIdStr=<arg>; playerIdType=1; playerId=479353'
            if (
                return_private
                and (match := re.search(
                    r'playerIdType=([0-9]{1,2}); playerId=([0-9]+)', ret_msg
                ))
            ):
                return PartialPlayer(
                    self, id=cast(responses.IntStr, match.group(2)),
                    platform=match.group(1),
                    private=True,
                )
            raise Private
        return Player(self, player_data)

    @overload
    async def get_players(
        self, player_ids: Iterable[int], *, return_private: Literal[False] = False
    ) -> Sequence[Player]:
        ...

    @overload
    async def get_players(
        self, player_ids: Iterable[int], *, return_private: Literal[True]
    ) -> Sequence[Player | PartialPlayer]:
        ...

    async def get_players(
        self, player_ids: Iterable[int], *, return_private: bool = False
    ) -> Sequence[Player | PartialPlayer]:
        """
        Fetches multiple players in a batch, and returns their list. Removes duplicates.

        Uses up a single request for every multiple of 20 unique player IDs passed.

        Parameters
        ----------
        player_ids : Iterable[int]
            An iterable of player IDs you want to fetch.
        return_private : bool
            When set to `True` and one of the requested profiles is determined private,
            this method will return a `PartialPlayer` object with the player ID and privacy flag
            set.\n
            When set to `False`, private profiles are omitted from the output list.\n
            Defaults to `False`.

        Returns
        -------
        list[Player | PartialPlayer]
            A list of players requested.\n
            `PartialPlayer` objects are only returned for private profiles and appropriate
            arguments used.
            Some players might not be included in the output if they weren't found,
            or their profile was private.
        """
        ids_list: list[int] = _deduplicate(player_ids, 0)  # also remove private accounts
        if not ids_list:
            return []
        # verify the types
        for player_id in ids_list:
            if not isinstance(player_id, int):
                raise TypeError(
                    f"Incorrect type found in the iterable: int expected, got {type(player_id)}"
                )
        logger.info(
            f"api.get_players(player_ids=[{', '.join(map(str, ids_list))}], {return_private=})"
        )
        player_list: list[Player | PartialPlayer] = []
        for chunk_ids in chunk(ids_list, 20):
            chunk_response = await self.request("getplayerbatch", ','.join(map(str, chunk_ids)))
            chunk_players: list[Player | PartialPlayer] = []
            for p in chunk_response:
                ret_msg = p["ret_msg"]
                if not ret_msg:
                    # We're good, just pack it up
                    chunk_players.append(Player(self, p))
                elif return_private and (match := re.search(r'playerId=([0-9]+)', ret_msg)):
                    # Pack up a private player object
                    chunk_players.append(
                        PartialPlayer(
                            self, id=cast(responses.IntStr, match.group(1)), private=True
                        )
                    )
            chunk_players.sort(key=lambda p: chunk_ids.index(p.id))
            player_list.extend(chunk_players)
        return player_list

    async def search_players(
        self,
        player_name: str,
        platform: Platform | None = None,
        *,
        return_private: bool = True,
        exact: bool = True,
    ) -> list[PartialPlayer]:
        """
        Fetches all players whose name matches the name specified.
        The search is fuzzy - player name capitalisation doesn't matter.

        Uses up a single request.

        .. note::

            Searching on all platforms will limit the number of players returned to ~500.
            Specifying a particular platform does not have this limitation.

        Parameters
        ----------
        player_name : str
            Player name you want to search for.
        platform : Platform | None
            Platform you want to limit the search to.\n
            Specifying `None` will search on all platforms.\n
            Defaults to `None`.
        return_private : bool
            When set to `True` and one of the requested profiles is determined private,
            this method will return a `PartialPlayer` object with the player ID and privacy flag
            set.\n
            When set to `False`, private profiles are omitted from the output list.\n
            Defaults to `True`.
        exact : bool
            When set to `True`, only players whose name matches exactly the name provided,
            will be returned.\n
            When set to `False`, exact matches will be returned first, followed by players
            whose name starts with the privided string.\n
            Defaults to `True`.

            .. warning::

                Settings this to `False` will limit the number of players returned to ~500.

        Returns
        -------
        list[PartialPlayer]
            A list of players whose name (and optionally platform) matches
            the specified name.\n
            Note that some of them might be set as private, unless appropriate input parameters
            were used.

        Raises
        ------
        NotFound
            There was no player for the given name (and optional platform) found.
        """
        # fail early for an incorrect player_name or platform type
        if not isinstance(player_name, str):
            raise TypeError(f"player_name argument has to be of str type, got {type(player_name)}")
        if platform is not None and not isinstance(platform, Platform):
            raise TypeError(
                "platform argument has to be None or of arez.Platform type, "
                f"got {type(platform)!r}"
            )
        list_response: list[responses.PartialPlayerObject]
        logger.info(
            f"api.search_players({player_name=}, platform={getattr(platform, 'name', None)}, "
            f"{return_private=}, {exact=})"
        )
        if exact and platform is not None:
            # Specific platform
            if platform in PC_PLATFORMS:
                # PC platforms, with unique names
                list_response = await self.request("getplayeridbyname", player_name)
            else:
                # Console platforms, names might be duplicated
                list_response = await self.request(
                    "getplayeridsbygamertag", platform.value, player_name
                )
        else:
            # All platforms or not exact
            response = await self.request("searchplayers", player_name)
            player_name = player_name.lower()
            list_response = []
            for player_dict in response:
                # reassign the names to prioritize unique PC names over console ones first
                if name := player_dict["hz_player_name"]:
                    player_dict["Name"] = name
                # if we're doing an exact name match and the current name isn't one, skip it
                if exact and player_dict["Name"].lower() != player_name:
                    continue
                # if a platform has been passed and it doesn't match, skip it
                if platform is not None and int(player_dict["portal_id"]) != platform.value:
                    continue
                list_response.append(player_dict)
        if not return_private:
            # Exclude private accounts
            list_response = [p for p in list_response if p["privacy_flag"] != 'y']
        if not list_response:
            raise NotFound("Player")
        return [
            PartialPlayer(
                self,
                id=p["player_id"],
                name=p["Name"],
                platform=p["portal_id"],
                private=p["privacy_flag"] == 'y',
            )
            for p in list_response
        ]

    async def get_from_platform(
        self, platform_id: int, platform: Platform
    ) -> PartialPlayer:
        """
        Fetches a PartialPlayer linked with the platform ID specified.

        .. note::

            This method doesn't set the `PartialPlayer.name` attribute, meaning that it will
            remain as an empty string. This is a limitation of the Hi-Rez API, not the library.

        Uses up a single request.

        Parameters
        ----------
        platform_id : int
            The platform-specific ID of the linked player.\n
            This is usually a Hi-Rez account ID, SteamID64, Discord User ID, etc.
        platform : Platform
            The platform this ID is for.

        Returns
        -------
        PartialPlayer
            The player this platform ID is linked to.

        Raises
        ------
        NotFound
            The linked profile doesn't exist / couldn't be found.
        """
        if not isinstance(platform_id, int):
            raise TypeError(f"platform_id argument has to be of int type, got {type(platform_id)}")
        if not isinstance(platform, Platform):
            raise TypeError(
                f"platform argument has to be of arez.Platform type, got {type(platform)!r}"
            )
        logger.info(f"api.get_from_platform({platform_id=}, platform={platform.name})")
        response = await self.request("getplayeridbyportaluserid", platform.value, platform_id)
        if not response:
            raise NotFound("Linked profile")
        p = response[0]
        return PartialPlayer(
            self, id=p["player_id"], platform=p["portal_id"], private=p["privacy_flag"] == 'y'
        )

    async def get_match(
        self, match_id: int, language: Language | None = None, *, expand_players: bool = False
    ) -> Match:
        """
        Fetches a match for the given Match ID.

        Uses up a single request.

        Parameters
        ----------
        match_id : int
            Match ID you want to get a match for.
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.
        expand_players : bool
            When set to `True`, partial player objects in the returned match object will
            automatically be expanded into full `Player` objects, if possible.\n
            Uses an addtional request to do the expansion.\n
            Defaults to `False`.

        Returns
        -------
        Match
            A match for the ID specified.

        Raises
        ------
        NotFound
            The match wasn't available on the server.\n
            This can happen if the match is older than 30 days, or is currently in progress.
        """
        if not isinstance(match_id, int):
            raise TypeError(f"match_id argument has to be of int type, got {type(match_id)}")
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._default_language
        cache_entry = await self._fetch_entry(language)
        logger.info(f"api.get_match({match_id=}, language={language.name}, {expand_players=})")
        response = await self.request("getmatchdetails", match_id)
        if not response:
            raise NotFound("Match")
        players_dict: dict[int, Player] = {}
        if expand_players:
            players_dict = await _get_players(self, (int(p["playerId"]) for p in response))
        return Match(self, cache_entry, response, players_dict)

    async def get_matches(
        self,
        match_ids: Iterable[int],
        language: Language | None = None,
        *,
        expand_players: bool = False,
    ) -> list[Match]:
        """
        Fetches multiple matches in a batch, for the given Match IDs. Removes duplicates.

        Uses up a single request for every multiple of 10 unique match IDs passed.

        Parameters
        ----------
        match_ids : Iterable[int]
            An iterable of Match IDs you want to fetch.
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.
        expand_players : bool
            When set to `True`, partial player objects in the returned match objects will
            automatically be expanded into full `Player` objects, if possible.\n
            Uses an addtional request for every 20 unique players to do the expansion.\n
            Defaults to `False`.

        Returns
        -------
        list[Match]
            A list of the available matches requested.\n
            Some of the matches can be not present if they weren't available on the server.
        """
        ids_list: list[int] = _deduplicate(match_ids)
        if not ids_list:
            return []
        # verify the types
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._default_language
        for match_id in ids_list:
            if not isinstance(match_id, int):
                raise TypeError(
                    f"Incorrect type found in the iterable: int expected, got {type(match_id)}"
                )
        cache_entry = await self._fetch_entry(language)
        logger.info(
            f"api.get_matches(match_ids=[{', '.join(map(str, ids_list))}], "
            f"language={language.name}, {expand_players=})"
        )
        matches: list[Match] = []
        players: dict[int, Player] = {}
        for chunk_ids in chunk(ids_list, 10):  # chunk the IDs into groups of 10
            response = await self.request("getmatchdetailsbatch", ','.join(map(str, chunk_ids)))
            # see if there are any API errors
            for mpd in response:
                if mpd["ret_msg"]:  # pragma: no cover
                    raise HTTPException(description=(
                        "Error in the 'getmatchdetailsbatch' endpoint!\n"
                        f"Match IDs: {','.join(map(str, chunk_ids))}\n"
                        f"Details: '{mpd['ret_msg']}'"
                    ))
            bunched_matches = group_by(response, lambda mpd: mpd["Match"])
            if expand_players:
                player_ids = []
                for p in response:
                    pid = int(p["playerId"])
                    if pid not in players:  # pragma: no branch
                        player_ids.append(pid)
                players_list = await self.get_players(player_ids)
                players.update({p.id: p for p in players_list})
            chunked_matches: list[Match] = [
                Match(self, cache_entry, match_list, players)
                for match_list in bunched_matches.values()
            ]
            matches.extend(chunked_matches)
        return matches

    async def get_matches_for_queue(
        self,
        queue: Queue,
        *,
        start: datetime,
        end: datetime,
        language: Language | None = None,
        region: Region | None = None,
        reverse: bool = False,
        local_time: bool = False,
        expand_players: bool = False,
    ) -> AsyncGenerator[Match, None]:
        """
        Creates an async generator that lets you iterate over all matches played
        in a particular queue, between the timestamps provided.

        Uses up a single request for every:\n
        • multiple of 10 matches fetched, according to the following points\n
        • 1 day worth of matches fetched, between midnights\n
        • 1 hour worth of matches fetched, between round hours\n
        • 10 minutes worth of matches fetched, between round 10 minutes intervals

        Depending on the timestamps provided, the longest possible fetching interval is used.

        .. note::

            To avoid wasting requests, it's recommended to invoke this generator with timestamps
            representing at least 10 minutes long interval, rounded to the nearest multiple of
            10 minutes. Being more granular will still work, and only return matches that
            fit within the interval specified though.

        .. note::

            Both naive and aware objects are supported for the timestamps.
            Aware objects will be internally converted to UTC according to their timezone.
            Naive objects are assumed to represent UTC time already, unless the ``local_time``
            argument is used.

        Parameters
        ----------
        queue : Queue
            The `Queue` you want to fetch the matches for.
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.
        region : Region | None
            The `Region` to filter the returned matches to.\n
            `None` means that no filtering is done.
        start : datetime.datetime
            A timestamp indicating the starting point of a time slice you want to
            fetch the matches in.
        end : datetime.datetime
            A timestamp indicating the ending point of a time slice you want to
            fetch the matches in.
        reverse : bool
            Reverses the order of the matches being returned.\n
            Defaults to `False`.
        local_time : bool
            When set to `True`, the timestamps provided are assumed to represent the local system
            time (in your local timezone), and will be converted to UTC before processing.\n
            When set to `False`, the timestamps provided are assumed to already represent UTC and
            no conversion will occur.\n
            Defaults to `False`.
        expand_players : bool
            When set to `True`, partial player objects in the returned match object will
            automatically be expanded into full `Player` objects, if possible.\n
            Uses an addtional request for every 20 unique players to do the expansion.\n
            Defaults to `False`.

        Returns
        -------
        AsyncGenerator[Match, None]
            An async generator yielding matches played in the queue specified, between the
            timestamps specified, optionally filtered to the region specified.
        """
        if not isinstance(queue, Queue):
            raise TypeError(f"queue argument has to be of arez.Queue type, got {type(queue)}")
        if language is not None and not isinstance(language, Language):
            raise TypeError(
                f"language argument has to be None or of arez.Language type, got {type(language)}"
            )
        if language is None:
            language = self._default_language
        # process start and end timestamps
        if start.tzinfo is not None or local_time:
            # assume local timezone, convert into UTC
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        if end.tzinfo is not None or local_time:
            end = end.astimezone(timezone.utc).replace(tzinfo=None)
        # exit early for a negative interval
        if end < start:
            return
        cache_entry = await self._fetch_entry(language)
        logger.info(
            f"api.get_matches_for_queue({queue=}, language={language.name}, "
            f"{start=} UTC, {end=} UTC, {reverse=}, {local_time=}, {expand_players=})"
        )

        # Use the generated date and hour values to iterate over and fetch matches
        players: dict[int, Player] = {}
        for date, hour in _date_gen(start, end, reverse=reverse):  # pragma: no branch
            queue_response = await self.request("getmatchidsbyqueue", queue.value, date, hour)
            processed: list[tuple[int, datetime, Region]] = sorted(
                (
                    (
                        int(match_info["Match"]),
                        _convert_timestamp(match_info["Entry_Datetime"]),
                        Region(match_info["Region"]),
                    )
                    for match_info in queue_response
                    if match_info["Active_Flag"] == 'n'
                ),
                key=itemgetter(1),
                reverse=reverse,
            )
            # gather and filter match IDs
            match_ids: list[int] = []
            if reverse:
                for mid, stamp, match_region in processed:  # pragma: no branch
                    if stamp < start:
                        break
                    if stamp <= end and (region is None or match_region == region):
                        match_ids.append(mid)
            else:
                for mid, stamp, match_region in processed:  # pragma: no branch
                    if stamp > end:
                        break
                    if stamp >= start and (region is None or match_region == region):
                        match_ids.append(mid)
            # fetch in chunks
            for chunk_ids in chunk(match_ids, 10):  # pragma: no branch
                matches_response = await self.request(
                    "getmatchdetailsbatch", ','.join(map(str, chunk_ids))
                )
                matches_response = list(filter(lambda i: not i["ret_msg"], matches_response))
                # see if there are any API errors
                for mpd in matches_response:
                    if mpd["ret_msg"]:  # pragma: no cover
                        raise HTTPException(description=(
                            "Error in the 'getmatchdetailsbatch' endpoint!\n"
                            f"Match IDs: {','.join(map(str, chunk_ids))}\n"
                            f"Details: '{mpd['ret_msg']}'"
                        ))
                bunched_matches = group_by(matches_response, lambda mpd: mpd["Match"])
                if expand_players:
                    player_ids = []
                    for p in matches_response:
                        pid = int(p["playerId"])
                        if pid not in players:  # pragma: no branch
                            player_ids.append(pid)
                    players_dict = await _get_players(self, player_ids)
                    players.update(players_dict)
                chunked_matches = [
                    Match(self, cache_entry, match_list, players)
                    for match_list in bunched_matches.values()
                ]
                chunked_matches.sort(key=lambda m: chunk_ids.index(m.id))
                for match in chunked_matches:
                    yield match

    async def get_bounty(
        self, *, language: Language | None = None
    ) -> tuple[list[BountyItem], list[BountyItem]]:
        """
        Returns a 2-item tuple denoting the (active, past) bounty store items, sorted
        by their expiration time.

        .. note:

            The "active" items list is sorted so that the first ``N`` items are the ones
            that are currently available, with further items becoming available
            once the current expire. ``N`` changes according to the in-game bounty store size.
            The "past" items list is sorted so that the first item
            is the one that has the most recently expired, with older items following.

        .. note:

            Depending on the bounty store availability, all active items may have already expired.
            If this happens, the "active" list is going to be empty,
            and the "past" items list will contain all expired items, as expected.

        Parameters
        ----------
        language : Language | None
            The `Language` you want to fetch the information in.\n
            Default language is used if not provided.

        Returns
        -------
        tuple[list[BountyItem], list[BountyItem]]
            An tuple containing a list of active bounty store items,
            followed by a list of items that have already expired.

        Raises
        ------
        NotFound
            No bounty items were returned.\n
            This can happen if the bounty store is unavailable for a long time.
        """
        cache_entry = await self._fetch_entry(language)
        response = await self.request("getbountyitems")
        if not response:
            raise NotFound("Bounty items")
        items = [BountyItem(self, cache_entry, item_data) for item_data in response]
        idx: int = 0
        for i, item in enumerate(items):  # pragma: no branch
            if not item.active:
                idx = i
                break
        return (items[idx-len(items)-1::-1], items[idx:])
