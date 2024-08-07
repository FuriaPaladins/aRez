from __future__ import annotations

import aiohttp
import asyncio
import logging
from hashlib import md5
from random import gauss
from platform import python_version
from datetime import timedelta
import datetime
from typing import Any, Literal, overload

from . import responses
from .utils import CacheDict
from . import __version__, __author__
from .exceptions import HTTPException, Unauthorized, Unavailable, LimitReached

__all__ = ["Endpoint"]


logger = logging.getLogger(__package__)
SESSION_LIFETIME = timedelta(minutes=15)
USER_AGENT = f"Python {python_version()}: aRez {__version__} by {__author__}"


def _timeout(total: int) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=total, connect=5)


_BASE_TIMEOUTS: CacheDict[int, aiohttp.ClientTimeout] = CacheDict(_timeout)
DEFAULT_TIMEOUT = _BASE_TIMEOUTS[5]

TIMEOUTS: dict[str, aiohttp.ClientTimeout] = {
    # default timeout is 5s
    "getgods": _BASE_TIMEOUTS[10],
    "getitems": _BASE_TIMEOUTS[10],
    "getchampions": _BASE_TIMEOUTS[10],
    "searchplayers": _BASE_TIMEOUTS[10],
    "getqueuestats": _BASE_TIMEOUTS[10],
    "getbountyitems": _BASE_TIMEOUTS[10],
    "getmatchdetails": _BASE_TIMEOUTS[10],
    "getmatchhistory": _BASE_TIMEOUTS[10],
    "getplayeridbyname": _BASE_TIMEOUTS[10],
    "getplayerloadouts": _BASE_TIMEOUTS[10],
    "getmatchplayerdetails": _BASE_TIMEOUTS[10],
    "getplayeridsbygamertag": _BASE_TIMEOUTS[10],
    "getplayeridbyportaluserid": _BASE_TIMEOUTS[10],
    "getfriends": _BASE_TIMEOUTS[20],
    "getplayerbatch": _BASE_TIMEOUTS[20],
    "getmatchidsbyqueue": _BASE_TIMEOUTS[20],
    "getmatchdetailsbatch": _BASE_TIMEOUTS[20],
    # hopefully long enough for the backend to process the data
    "getchampionskins": _BASE_TIMEOUTS[30],
}


def _get_timeout(method_name: str) -> aiohttp.ClientTimeout:
    return TIMEOUTS.get(method_name, DEFAULT_TIMEOUT)


class Endpoint:
    """
    Represents a basic Hi-Rez endpoint URL wrapper, for handling response types and
    session creation.

    .. note::

        You can request your developer ID and authorization key `here.
        <https://fs12.formsite.com/HiRez/form48/secure_index.html>`_

    .. warning::

        The main API and data cache classes use this class as base, so all of it's methods
        are already available there. This class is listed here solely for documentation purposes.
        Instanting it yourself is possible, but not recommended.

    Parameters
    ----------
    url : str
        The endpoint's base URL.
    dev_id : int | str
        Your developer's ID (devId).
    auth_key : str
        Your developer's authentication key (authKey).
    loop : asyncio.AbstractEventLoop | None
        The event loop you want to use for this Endpoint.\n
        Default loop is used when not provided.
    """
    def __init__(
        self,
        url: str,
        dev_id: int | str,
        auth_key: str,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        if loop is None:  # pragma: no cover
            loop = asyncio.get_event_loop()
        self._loop = loop
        self.url = url.rstrip('/')
        self._session_key = ''
        self._session_lock = asyncio.Lock()
        self._session_expires = datetime.datetime.now(datetime.UTC)
        self._http_session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT, loop=loop
        )
        self.__dev_id = str(dev_id)
        self.__auth_key = auth_key.upper()

    def __del__(self):  # pragma: no cover
        self._http_session.detach()

    async def close(self):
        """
        Closes the underlying API connection.

        Attempting to make a request after the connection is closed
        will result in a `RuntimeError`.
        """
        await self._http_session.close()

    async def __aenter__(self) -> Endpoint:  # pragma: no cover
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        # use local close - this handles subclased close method too
        await self.close()

    def _get_signature(self, method_name: str, timestamp: str):
        return md5(
            ''.join((self.__dev_id, method_name, self.__auth_key, timestamp)).encode()
        ).hexdigest()

    # API ping
    @overload
    async def request(self, method_name: Literal["ping"], /) -> str:
        ...

    # session creation
    @overload
    async def request(self, method_name: Literal["createsession"], /) -> responses.SessionObject:
        ...

    # session testing
    # note - session_key is optional, internal session is used if not provided
    @overload
    async def request(self, method_name: Literal["testsession"], session_key: str = '', /) -> str:
        ...

    # patch info
    @overload
    async def request(self, method_name: Literal["getpatchinfo"], /) -> responses.PatchInfoObject:
        ...

    # API usage stats
    @overload
    async def request(
        self, method_name: Literal["getdataused"], /,
    ) -> list[responses.DataUsedObject]:
        ...

    # server status
    @overload
    async def request(
        self, method_name: Literal["gethirezserverstatus"], /,
    ) -> list[responses.ServerStatusObject]:
        ...

    # champions data
    @overload
    async def request(
        self, method_name: Literal["getgods", "getchampions"], language_value: int, /,
    ) -> list[responses.ChampionObject]:
        ...

    # items / devices data
    @overload
    async def request(
        self, method_name: Literal["getitems"], language_value: int, /,
    ) -> list[responses.DeviceObject]:
        ...

    # champion skins data
    @overload
    async def request(
        self,
        method_name: Literal["getchampionskins"],
        champion_id: int,
        language_value: int,
        /,
    ) -> list[responses.ChampionSkinObject]:
        ...

    # player information
    @overload
    async def request(
        self, method_name: Literal["getplayer", "getplayerbatch"], name_or_id: int | str, /,
    ) -> list[responses.PlayerObject]:
        ...

    # match information
    @overload
    async def request(
        self,
        method_name: Literal["getmatchdetails", "getmatchdetailsbatch"],
        match_ids: int | str,  # single or comma-delimited string
        /,
    ) -> list[responses.MatchPlayerObject]:
        ...

    # live match information
    @overload
    async def request(
        self, method_name: Literal["getmatchplayerdetails"], match_id: int, /,
    ) -> list[responses.LivePlayerObject]:
        ...

    # partial players - PC platform only
    @overload
    async def request(
        self, method_name: Literal["getplayeridbyname"], name: str, /,
    ) -> list[responses.PartialPlayerObject]:
        ...

    # partial players - console platforms only
    @overload
    async def request(
        self,
        method_name: Literal["getplayeridsbygamertag", "getplayeridbyportaluserid"],
        portal_id: int,
        name_or_id: str | int,
        /,
    ) -> list[responses.PartialPlayerObject]:
        ...

    # searching players
    @overload
    async def request(
        self, method_name: Literal["searchplayers"], name: str, /,
    ) -> list[responses.PlayerSearchObject]:
        ...

    # getting match IDs by queue
    @overload
    async def request(
        self,
        method_name: Literal["getmatchidsbyqueue"],
        queue_value: int,
        date: str,
        hour: str,
        /,
    ) -> list[responses.MatchSearchObject]:
        ...

    # bounty items
    @overload
    async def request(
        self, method_name: Literal["getbountyitems"], /,
    ) -> list[responses.BountyItemObject]:
        ...

    # player status
    @overload
    async def request(
        self, method_name: Literal["getplayerstatus"], player_id: int, /,
    ) -> list[responses.PlayerStatusObject]:
        ...

    # player friends
    @overload
    async def request(
        self, method_name: Literal["getfriends"], player_id: int, /,
    ) -> list[responses.PlayerFriendObject]:
        ...

    # player's champion loadouts
    @overload
    async def request(
        self,
        method_name: Literal["getplayerloadouts"],
        player_id: int,
        language_value: int,
        /,
    ) -> list[responses.ChampionLoadoutObject]:
        ...

    # overall god / champion stats
    @overload
    async def request(
        self, method_name: Literal["getgodranks", "getchampionranks"], player_id: int, /,
    ) -> list[responses.ChampionRankObject]:
        ...

    # per-queue champion stats
    @overload
    async def request(
        self, method_name: Literal["getqueuestats"], player_id: int, queue_value: int, /,
    ) -> list[responses.ChampionQueueRankObject]:
        ...

    # player's history matches
    @overload
    async def request(
        self, method_name: Literal["getmatchhistory"], player_id: int, /,
    ) -> list[responses.HistoryMatchObject]:
        ...

    # general case
    @overload
    async def request(self, method_name: str, /, *data: str | int) -> Any:
        ...

    async def request(self, method_name: str, /, *data: str | int) -> Any:
        """
        Makes a direct request to the HiRez API.

        For all methods available (and their parameters), `please see Hi-Rez API docs.
        <https://docs.google.com/document/d/1OFS-3ocSx-1Rvg4afAnEHlT3917MAK_6eJTR6rzr-BM>`_

        Parameters
        ----------
        method_name : str
            The name of the method requested. This shouldn't include the reponse type as it's
            added for you.
        *data : int | str
            Method parameters requested to add at the end of the request, if applicable.
            These should be either integers or strings.

        Returns
        -------
        str | dict[str, Any] | list[dict[str, Any]]
            A raw server's response as a string, list or a dictionary (depending on the endpoint).

        Raises
        ------
        HTTPException
            Whenever it was impossible to fetch the data in a reliable manner.\n
            Check the `HTTPException.cause` attribute for the original exception (and reason)
            that lead to this.
        Unauthorized
            When the developer's ID (devId) or the developer's authentication key (authKey)
            are deemed invalid.
        Unavailable
            When the Hi-Rez API switches to emergency mode, and no data could be returned
            at this time.
        LimitReached
            Your daily limit of requests has been reached.
        """
        last_exc = None
        method_name = method_name.lower()

        for tries in range(5):  # pragma: no branch
            try:
                # prepare the URL
                req_stack = [self.url, f"{method_name}json"]
                if method_name == "createsession":
                    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
                    req_stack.extend(
                        (self.__dev_id, self._get_signature(method_name, timestamp), timestamp)
                    )
                elif method_name == "testsession" and len(data) == 1 and data[0]:
                    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
                    req_stack.extend((
                        self.__dev_id,
                        self._get_signature(method_name, timestamp),
                        str(data[0]),
                        timestamp,
                    ))
                elif method_name != "ping":
                    async with self._session_lock:
                        now = datetime.datetime.now(datetime.UTC)
                        if now >= self._session_expires:
                            session_response = await self.request("createsession")  # recursion
                            session_id = session_response.get("session_id")
                            if not session_id:
                                raise Unauthorized
                            self._session_key = session_id
                        self._session_expires = now + SESSION_LIFETIME
                    # reacquire the current time
                    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
                    req_stack.extend((
                        self.__dev_id,
                        self._get_signature(method_name, timestamp),
                        self._session_key,
                        timestamp,
                    ))
                    if data:
                        req_stack.extend(map(str, data))
                req_url = '/'.join(req_stack)
                logger.debug(f"endpoint.request: {method_name}: {req_url}")

                # request
                async with self._http_session.get(
                    req_url, timeout=_get_timeout(method_name)
                ) as response:
                    # Handle special HTTP status codes
                    if response.status == 503:
                        # '503: Service Unavailable'
                        raise Unavailable
                    # Raise for any other error code
                    response.raise_for_status()
                    res_data: str | list[dict[str, Any]] | dict[str, Any] = await response.json()

                # handle some ret_msg errors, if possible
                if res_data:
                    if isinstance(res_data, list) and isinstance(res_data[0], dict):
                        error = res_data[0].get("ret_msg")
                    elif isinstance(res_data, dict):
                        error = res_data.get("ret_msg")
                    else:
                        error = None
                    if error:
                        # Invalid session
                        if error == "Invalid session id.":
                            # Invalidate the current session by expiring it, then retry
                            self._session_expires = datetime.datetime.now(datetime.UTC)
                            continue
                        # Daily limit reached
                        elif error == "Daily request limit reached":
                            raise LimitReached

                return res_data

            # When connection problems happen, just give the api a short break and try again.
            except (
                aiohttp.ClientConnectionError, asyncio.TimeoutError
            ) as exc:  # pragma: no cover
                last_exc = exc  # store for the last iteration raise
                if isinstance(exc, asyncio.TimeoutError):
                    logger.warning("Timed out, retrying...")
                elif isinstance(exc, aiohttp.ServerDisconnectedError):
                    logger.warning("Server disconnected, retrying...")
                else:
                    logger.warning("Connection problems, retrying...")
                # pass and retry on the next loop
            # When '.raise_for_status()' generates this one, just wrap it and raise
            except aiohttp.ClientResponseError as exc:
                logger.exception("Got a response error")
                raise HTTPException(exc)
            # When "createsession" raises these recursively - log and pass those along
            except (
                HTTPException, Unauthorized, Unavailable, LimitReached
            ) as exc:  # pragma: no branch
                if isinstance(exc, Unavailable):
                    logger.warning("Hi-Rez API is Unavailable")
                if isinstance(exc, Unauthorized):
                    logger.error("You are Unauthorized")
                elif isinstance(exc, LimitReached):  # pragma: no branch
                    logger.error("Daily request limit reached")
                # don't log HTTPExceptions here
                raise
            # Some other exception happened, so just wrap it and propagate along
            except Exception as exc:  # pragma: no cover
                logger.exception("Got an unexpected exception")
                raise HTTPException(exc)

            # Sleep before retrying
            await asyncio.sleep(tries * 0.5 * gauss(1, 0.1))  # pragma: no cover

        # we've run out of tries, so ¯\_(ツ)_/¯
        # we shouldn't ever end up here, this is a fail-safe
        logger.exception("Ran out of retries", exc_info=last_exc)  # pragma: no cover
        raise HTTPException(last_exc)  # pragma: no cover
