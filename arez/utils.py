from __future__ import annotations

import sys
from math import floor
from difflib import SequenceMatcher
from functools import partialmethod
from weakref import WeakValueDictionary
from datetime import datetime, timedelta
from collections import abc, OrderedDict, ChainMap
from operator import itemgetter, attrgetter, eq, ne, lt, le, gt, ge
from typing import (
    Any,
    Optional,
    Union,
    List,
    Dict,
    Tuple,
    Literal,
    Mapping,
    Callable,
    Iterable,
    Iterator,
    Sequence,
    Generator,
    AsyncGenerator,
    TypeVar,
    Generic,
    cast,
    overload,
)

from .mixins import CacheObject, Expandable


__all__ = [
    # functions
    "get",
    "chunk",
    "group_by",
    "expand_partial",
    # classes
    "Lookup",
    "Duration",
    "LookupGroup",
    "WeakValueDefaultDict",
]
# Type variable for internal utils typing
_X = TypeVar("_X")
_Y = TypeVar("_Y")
LookupType = TypeVar("LookupType")
LookupKeyType = TypeVar("LookupKeyType", bound=CacheObject)


def _deduplicate(iterable: Iterable[_X], *to_remove: _X) -> list[_X]:
    """
    Removes duplicates from an iterable and returns a list. Optimised for speed.
    Optionally, also removes the value(s) specified entirely.

    Parameters
    ----------
    iterable : Iterable[X]
        The iterable of values to deduplicate.
    *to_remove: X
        Optional value(s) to remove.

    Returns
    -------
    list[X]
        The deduplicated list of values.
    """
    if not isinstance(iterable, Iterable):
        raise TypeError(f"Expected an iterable, got {type(iterable)}")
    no_dups: list[_X] = list(OrderedDict.fromkeys(iterable))
    for value in to_remove:
        if value in no_dups:
            no_dups.remove(value)
    return no_dups


def _convert_timestamp(timestamp: str) -> datetime:
    """
    Converts the timestamp format returned by the API.

    Parameters
    ----------
    timestamp : str
        The string containing the timestamp.

    Returns
    -------
    datetime
        A converted datetime object.
    """
    return datetime.strptime(timestamp, "%m/%d/%Y %I:%M:%S %p")


def _convert_map_name(map_name: str) -> str:
    """
    Converts the map name, removing the unneeded prefixes.

    Parameters
    ----------
    map_name : str
        The string representing the map name.

    Returns
    -------
    str
        The converted map name.
    """
    map_name = map_name.strip()
    for prefix in ("LIVE", "Ranked", "Practice", "WIP"):  # pragma: no branch
        if map_name.startswith(prefix):
            map_name = map_name[len(prefix):]
            break
    for suffix in ("(Siege)", "(Onslaught)", "(TDM)", "(KOTH)"):
        if map_name.endswith(suffix):
            map_name = map_name[:-len(suffix)]
            break
    return map_name.strip()


def _floor_dt(dt: datetime, td: timedelta) -> datetime:
    return dt - (dt - datetime.min) % td


def _ceil_dt(dt: datetime, td: timedelta) -> datetime:
    return dt + (datetime.min - dt) % td


# Generates API-valid series of date and hour parameters for the 'getmatchidsbyqueue' endpoint
def _date_gen(
    start: datetime, end: datetime, *, reverse: bool = False
) -> Generator[tuple[str, str], None, None]:
    # helpful time intervals
    one_day = timedelta(days=1)
    one_hour = timedelta(hours=1)
    ten_minutes = timedelta(minutes=10)
    # round start and end to the nearest multiply of 10m
    # floor start and ceil end
    start = _floor_dt(start, ten_minutes)
    end = _ceil_dt(end, ten_minutes)
    # check if the time slice is too short - save on processing by quitting early
    if start >= end:
        return

    if reverse:
        if end.minute > 0:
            # round down end to the nearest hour
            closest_hour = _floor_dt(end, one_hour)
            while end > closest_hour:
                end -= ten_minutes
                yield (end.strftime("%Y%m%d"), f"{end.hour},{end.minute:02}")
                if end <= start:
                    return
        if end.hour > 0:
            # round down end to the nearest day midnight
            closest_day = _floor_dt(end, one_day)
            if closest_day >= start:
                while end > closest_day:
                    end -= one_hour
                    yield (end.strftime("%Y%m%d"), str(end.hour))
                    if end <= start:
                        return
        # round up start to the nearest end day midnight
        closest_day = _ceil_dt(start, one_day)
        while end > closest_day:
            end -= one_day
            yield (end.strftime("%Y%m%d"), "-1")
        if end <= start:
            return
        if start.hour > 0:
            # round up start to the nearest hour
            closest_hour = _ceil_dt(start, one_hour)
            while end > closest_hour:
                end -= one_hour
                yield (end.strftime("%Y%m%d"), str(end.hour))
            if end <= start:
                return
        # finish
        while end > start:
            end -= ten_minutes
            yield (end.strftime("%Y%m%d"), f"{end.hour},{end.minute:02}")
    else:
        if start.minute > 0:
            # round up start to the nearest hour
            closest_hour = _ceil_dt(start, one_hour)
            while start < closest_hour:
                yield (start.strftime("%Y%m%d"), f"{start.hour},{start.minute:02}")
                start += ten_minutes
                if start >= end:
                    return
        if start.hour > 0:
            # round up start to the nearest day midnight
            closest_day = _ceil_dt(start, one_day)
            if closest_day <= end:
                while start < closest_day:
                    yield (start.strftime("%Y%m%d"), str(start.hour))
                    start += one_hour
                    if start >= end:
                        return
        # round down end to the nearest end day midnight
        closest_day = _floor_dt(end, one_day)
        while start < closest_day:
            yield (start.strftime("%Y%m%d"), "-1")
            start += one_day
        if start >= end:
            return
        if end.hour > 0:
            # round down end to the nearest end hour
            closest_hour = _floor_dt(end, one_hour)
            while start < closest_hour:
                yield (start.strftime("%Y%m%d"), str(start.hour))
                start += one_hour
            if start >= end:
                return
        # finish
        while start < end:
            yield (start.strftime("%Y%m%d"), f"{start.hour},{start.minute:02}")
            start += ten_minutes


def get(iterable: Iterable[_X], **attrs) -> _X | None:
    """
    Returns the first object from the ``iterable`` which attributes match the
    keyword arguments passed.

    You can use ``__`` to search in nested attributes.

    Parameters
    ----------
    iterable : Iterable
        The iterable to search in.
    **attrs
        The attributes to search for.

    Returns
    -------
    Any
        The first object from the iterable with attributes matching the keyword arguments passed.\n
        `None` is returned if the desired object couldn't be found in the iterable.
    """
    if len(attrs) == 1:  # speed up checks for only one test atribute
        attr, val = attrs.popitem()
        getter = attrgetter(attr.replace('__', '.'))
        for element in iterable:
            if getter(element) == val:
                return element
        return None
    getters = [(attrgetter(attr.replace('__', '.')), val) for attr, val in attrs.items()]
    for element in iterable:
        for getter, val in getters:
            if getter(element) != val:
                break
        else:
            return element
    return None


def group_by(iterable: Iterable[_X], key: Callable[[_X], _Y]) -> dict[_Y, list[_X]]:
    """
    A helper function for grouping elements of an iterable into a dictionary, where each key
    represents a common value, and the value represents a list of elements having said
    common value.

    Parameters
    ----------
    iterable : Iterable[X]
        An iterable of elements to group.
    key : Callable[[X], Y]
        A function that takes each element from the provided iterable as it's parameter,
        and outputs a group to which said element belongs to.

    Returns
    -------
    dict[Y, list[X]]
        A mapping of groups to lists of grouped elements.
    """
    item_map: dict[_Y, list[_X]] = {}
    for item in iterable:
        group = key(item)
        if group not in item_map:
            item_map[group] = []
        item_map[group].append(item)
    return item_map


class _LookupBase(Sequence[LookupType], Generic[LookupKeyType, LookupType]):
    def __init__(
        self,
        iterable: Iterable[LookupType],
        *,
        key: Callable[[LookupType], LookupKeyType] = lambda item: item,  # type: ignore
    ):
        self._list_lookup: list[LookupType] = []
        self._id_lookup: dict[int, LookupType] = {}
        self._name_lookup: dict[str, LookupType] = {}
        self._cached_id_lookup: dict[int, CacheObject] = {}
        self._cached_name_lookup: dict[str, CacheObject] = {}
        self._id_chain_lookup: ChainMap[int, Any] = (
            ChainMap(self._id_lookup, self._cached_id_lookup)
        )
        self._name_chain_lookup: ChainMap[str, Any] = (
            ChainMap(self._name_lookup, self._cached_name_lookup)
        )
        self._key: Callable[[LookupType], LookupKeyType] = key

        for element in iterable:
            self.add(element)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({repr(self._list_lookup)})"

    def __len__(self) -> int:
        return len(self._list_lookup)

    def __iter__(self) -> Iterator[LookupType]:
        return iter(self._list_lookup)

    @overload
    def __getitem__(self, index: int) -> LookupType:
        ...

    @overload
    def __getitem__(self, index: slice) -> list[LookupType]:
        ...

    def __getitem__(self, index: int | slice) -> LookupType | list[LookupType]:
        return self._list_lookup[index]

    def __contains__(self, item: object) -> bool:
        return item in self._list_lookup

    def __reversed__(self) -> Iterator[LookupType]:
        return reversed(self._list_lookup)

    def index(self, item: LookupType, start: int = 0, stop: int = sys.maxsize) -> int:
        return self._list_lookup.index(item, start, stop)

    def count(self, item: LookupType) -> int:
        return self._list_lookup.count(item)

    def add(self, element: LookupType) -> None:
        """
        Subclasses should use this to implement a way of populating the invernal lookup dicts.
        """
        raise NotImplementedError

    @property
    def cached(self) -> list[CacheObject]:
        elements = set(self._cached_id_lookup.values())
        elements.update(self._cached_name_lookup.values())
        return list(elements)

    def _cache_object(
        self, id: int | None = None, name: str | None = None
    ) -> LookupType | CacheObject:
        """
        Internal function.

        Use the CacheObject data to substitute in a rich data object (Champion, Item, Talent, etc.)
        If the object isn't found in the cache, create a new CacheObject and return that instead.

        NOTE: This always creates new objects with the data provided.
        To only query the information instead, use the ``get`` method.
        """
        if id is not None:
            if not isinstance(id, int):
                raise ValueError("ID has to be an integer")
            obj = self.get(id)
        elif name is not None:
            if not isinstance(name, str):
                raise ValueError("Name has to be a string")
            obj = self.get(name)
        else:
            raise TypeError("Either ID or Name are required")
        if obj is not None:
            return cast(LookupType, obj)
        # fall back to a CacheObject
        # check if we can update existing objects with new information
        kwargs: dict[Any, Any] = {}
        element: CacheObject | None = None
        if id is not None:
            kwargs["id"] = id
            if id in self._cached_id_lookup:
                element = self._cached_id_lookup[id]
                if not element.is_default_name() or name is None:
                    return element
        if name is not None:
            kwargs["name"] = name
            if element is None and (lower_name := name.lower()) in self._cached_name_lookup:
                element = self._cached_name_lookup[lower_name]
                if not element.is_default_id() or id is None:
                    return element
        element = CacheObject(**kwargs)
        if not element.is_default_id():
            self._cached_id_lookup[element.id] = element
        if not element.is_default_name():
            self._cached_name_lookup[element.name.lower()] = element
        return element

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[False] = False
    ) -> LookupType | list[LookupType] | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[True]
    ) -> LookupType | CacheObject | list[LookupType] | list[LookupType | CacheObject] | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: bool = False
    ) -> LookupType | CacheObject | list[LookupType] | list[LookupType | CacheObject] | None:
        ...

    def get(
        self, name_or_id: int | str, *, with_cached: bool = False
    ) -> LookupType | list[LookupType] | None:
        if isinstance(name_or_id, int):
            if with_cached:
                return self._id_chain_lookup.get(name_or_id)
            return self._id_lookup.get(name_or_id)
        elif isinstance(name_or_id, str):
            if with_cached:
                return self._name_chain_lookup.get(name_or_id.lower())
            return self._name_lookup.get(name_or_id.lower())
        raise TypeError("Argument has to be of either int or str type")

    def get_fuzzy(
        self, name: str, *, cutoff: float = 0.6, with_cached: bool = False
    ) -> LookupType | CacheObject | list[LookupType] | list[LookupType | CacheObject] | None:
        matches = self.get_fuzzy_matches(name, limit=1, cutoff=cutoff, with_cached=with_cached)
        if matches:
            return matches[0]
        return None

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[False] = False,
    ) -> list[LookupType] | list[list[LookupType]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[False] = False,
    ) -> list[LookupType | CacheObject] | list[list[LookupType | CacheObject]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[True],
    ) -> list[tuple[LookupType, float]] | list[tuple[list[LookupType], float]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[True],
    ) -> (
        list[tuple[LookupType | CacheObject, float]]
        | list[tuple[list[LookupType | CacheObject], float]]
    ):
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: Literal[False] = False,
    ) -> (
        list[LookupType] | list[list[LookupType]]
        | list[LookupType | CacheObject] | list[list[LookupType | CacheObject]]
    ):
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[LookupType] | list[list[LookupType]]
        | list[LookupType | CacheObject] | list[list[LookupType | CacheObject]]
        | list[tuple[LookupType, float]] | list[tuple[list[LookupType], float]]
        | list[tuple[LookupType | CacheObject, float]]
        | list[tuple[list[LookupType | CacheObject], float]]
    ):
        ...

    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[LookupType] | list[list[LookupType]]
        | list[LookupType | CacheObject] | list[list[LookupType | CacheObject]]
        | list[tuple[LookupType, float]] | list[tuple[list[LookupType], float]]
        | list[tuple[LookupType | CacheObject, float]]
        | list[tuple[list[LookupType | CacheObject], float]]
    ):
        if not isinstance(name, str):
            raise TypeError("name has to be a string")
        if not isinstance(limit, int):
            raise TypeError("limit has to be a positive non-zero integer")
        if not isinstance(cutoff, float):
            raise TypeError("cutoff has to be a float in 0-1 range")
        # NOTE: The above are TypeError, below are ValueError
        if not limit > 0:
            raise ValueError("limit has to be a positive non-zero integer")
        if not 0 <= cutoff <= 1:
            raise ValueError("cutoff has to be a float in 0-1 range")

        seq_matcher: SequenceMatcher[str] = SequenceMatcher()
        seq_matcher.set_seq2(name.lower())
        scores: list[tuple[LookupType | CacheObject, float]] = []
        if not with_cached:
            lookup_bank: abc.Mapping[str, LookupType | CacheObject] = self._name_lookup
        else:
            lookup_bank = self._name_chain_lookup
        for key, element in lookup_bank.items():
            seq_matcher.set_seq1(key)
            if (
                seq_matcher.real_quick_ratio() >= cutoff
                and seq_matcher.quick_ratio() >= cutoff
                and (score := seq_matcher.ratio()) >= cutoff
            ):
                scores.append((element, score))
        scores.sort(key=itemgetter(1), reverse=True)
        if with_scores:
            return scores[:limit]
        return list(map(itemgetter(0), scores[:limit]))


class Lookup(_LookupBase[LookupKeyType, LookupType]):
    """
    A helper class utilizing an internal list and two dictionaries, allowing for easy indexing
    and lookup of `CacheObject <arez.CacheObject>` and it's subclasses,
    based on the Name and ID attributes. Supports fuzzy Name searches too.

    This object resembles an immutable sequence, and thus exposes ``__len__``, ``__iter__``,
    ``__getitem__``, ``__contains__``, ``__reversed__``, ``index`` and ``count``
    special methods for ease of use. The types specified refer to: ``LookupKeyType``
    by which you can query, and ``LookupType`` which is returned from the query.

    If you'd prefer a normal list instead, use: ``list(lookup)``.

    Parameters
    ----------
    iterable : Iterable[LookupType]
        The iterable to objects to transform into a lookup.
    key : Callable[[LookupType], LookupKeyType]
        The lookup key function, mapping each object to a `CacheObject <arez.CacheObject>`
        or it's subclass, by which the lookup should be indexed.\n
        Defaults to an identity function (``lambda item: item``), meaning objects passed
        as the iterable have to be a `CacheObject <arez.CacheObject>` or it's subclass already.
    """
    def add(self, element: LookupType) -> None:
        self._list_lookup.append(element)
        cache_key: LookupKeyType = self._key(element)
        if not isinstance(cache_key, CacheObject):
            raise ValueError(
                "Key callable needs to return a subclassed instance of CacheObject"
            )
        self._id_lookup[cache_key.id] = element
        self._name_lookup[cache_key.name.lower()] = element

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[False] = False
    ) -> LookupType | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[True]
    ) -> LookupType | CacheObject | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: bool = False
    ) -> LookupType | CacheObject | None:
        ...

    def get(
        self, name_or_id: int | str, with_cached: bool = False
    ) -> LookupType | CacheObject | None:
        """
        Allows you to quickly lookup an element by it's Name or ID.

        Parameters
        ----------
        name_or_id : int | str
            The name or ID of the element you want to lookup.

            .. note::

                The name lookup is case-insensitive.

        Returns
        -------
        LookupType | None
            The element requested.\n
            `None` is returned if the requested element couldn't be found.
        """
        return cast(Optional[LookupType], super().get(name_or_id, with_cached=with_cached))

    def get_fuzzy(
        self, name: str, *, cutoff: float = 0.6, with_cached: bool = False
    ) -> LookupType | CacheObject | None:
        """
        Simplified version of `get_fuzzy_matches`, allowing you to search for a single element,
        or receive `None` if no matching element was found.

        Parameters
        ----------
        name : str
            The name of the element you want to lookup.
        cutoff : float, optional
            The similarity score cutoff range. See: `get_fuzzy_matches` for more information.\n
            Defaults to ``0.6``.

        Returns
        -------
        LookupType | None
            The element requested.\n
            `None` is returned if the requested element couldn't be found.

        Raises
        ------
        TypeError
            ``name`` or ``cutoff`` arguments are of incorrect type
        ValueError
            ``cutoff`` argument has an incorrect value
        """
        return cast(
            Optional[Union[LookupType, CacheObject]],
            super().get_fuzzy(name, cutoff=cutoff, with_cached=with_cached),
        )

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[False] = False,
    ) -> list[LookupType]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[False] = False,
    ) -> list[LookupType | CacheObject]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[True],
    ) -> list[tuple[LookupType, float]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[True],
    ) -> list[tuple[LookupType | CacheObject, float]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: Literal[False] = False,
    ) -> list[LookupType] | list[LookupType | CacheObject]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[LookupType]
        | list[LookupType | CacheObject]
        | list[tuple[LookupType, float]]
        | list[tuple[LookupType | CacheObject, float]]
    ):
        ...

    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[LookupType]
        | list[LookupType | CacheObject]
        | list[tuple[LookupType, float]]
        | list[tuple[LookupType | CacheObject, float]]
    ):
        """
        Performs a fuzzy lookup of an element by it's name,
        by calculating the similarity score between each item. Case-insensitive.\n
        See also: `get_fuzzy`.

        Parameters
        ----------
        name : str
            The name of the element you want to lookup.
        limit : int
            The maximum amount of elements to return in the list. Has to be greater than ``0``.\n
            Defaults to ``3``.
        cutoff : float
            The similarity score cutoff range, below which matches will be excluded
            from the output. Lower values have a better chance of yielding correct results,
            but also a higher chance of false-positives. Accepted range is ``0`` to ``1``.\n
            Defaults to ``0.6``.
        with_scores : bool
            If set to `True`, returns a list of 2-item tuples, with the similar element
            as the first item, and its score as the second.\n
            Defaults to `False`.

        Returns
        -------
        list[LookupType] | list[tuple[LookupType, float]]
            A list of up to ``limit`` matching elements, with at least ``cutoff`` similarity score,
            sorted in descending order by their similarity score.\n
            If ``with_scores`` is set to `True`, returns a list of up to ``limit`` 2-item tuples,
            where the first item of each tuple is the element, and the second item
            is the similarity score it has.

        Raises
        ------
        TypeError
            ``name``, ``limit`` or ``cutoff`` arguments are of incorrect type
        ValueError
            ``limit`` or ``cutoff`` arguments have an incorrect value
        """
        return cast(
            Union[
                List[LookupType],
                List[Union[LookupType, CacheObject]],
                List[Tuple[LookupType, float]],
                List[Tuple[Union[LookupType, CacheObject], float]],
            ],
            super().get_fuzzy_matches(
                name, limit=limit, cutoff=cutoff, with_cached=with_cached, with_scores=with_scores
            ),
        )


class LookupGroup(_LookupBase[LookupKeyType, LookupType]):
    """
    This class is indentical to the `Lookup` class functionality-wise, but it's been made
    a separate class due to typing collisions. The only difference here is that `get`, `get_fuzzy`
    and `get_fuzzy_matches` methods return a grouped `list` of the specified type instances,
    instead of a single instance. Right now, this is used solely for
    the `PartialPlayer.get_loadouts` method return type, to be able to return a list of loadouts
    for each champion.
    """
    _id_lookup: dict[int, list[LookupType]] = {}  # type: ignore
    _name_lookup: dict[str, list[LookupType]] = {}  # type: ignore

    def add(self, element: LookupType) -> None:
        self._list_lookup.append(element)
        cache_key: LookupKeyType = self._key(element)
        if not isinstance(cache_key, CacheObject):
            raise ValueError(
                "Key callable needs to return a subclassed instance of CacheObject"
            )
        self._id_lookup.setdefault(cache_key.id, []).append(element)
        self._name_lookup.setdefault(cache_key.name.lower(), []).append(element)

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[False] = False
    ) -> list[LookupType] | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: Literal[True]
    ) -> list[LookupType] | list[LookupType | CacheObject] | None:
        ...

    @overload
    def get(
        self, name_or_id: int | str, *, with_cached: bool = False
    ) -> list[LookupType] | list[LookupType | CacheObject] | None:
        ...

    def get(
        self, name_or_id: int | str, *, with_cached: bool = False
    ) -> list[LookupType] | list[LookupType | CacheObject] | None:
        return cast(Optional[List[LookupType]], super().get(name_or_id, with_cached=with_cached))

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[False] = False,
    ) -> list[list[LookupType]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[False] = False,
    ) -> list[list[LookupType | CacheObject]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[False] = False,
        with_scores: Literal[True],
    ) -> list[tuple[list[LookupType], float]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: Literal[True],
        with_scores: Literal[True],
    ) -> list[tuple[list[LookupType | CacheObject], float]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: Literal[False] = False,
    ) -> list[list[LookupType]] | list[list[LookupType | CacheObject]]:
        ...

    @overload
    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[list[LookupType]]
        | list[list[LookupType | CacheObject]]
        | list[tuple[list[LookupType], float]]
        | list[tuple[list[LookupType | CacheObject], float]]
    ):
        ...

    def get_fuzzy_matches(
        self,
        name: str,
        *,
        limit: int = 3,
        cutoff: float = 0.6,
        with_cached: bool = False,
        with_scores: bool = False,
    ) -> (
        list[list[LookupType]]
        | list[list[LookupType | CacheObject]]
        | list[tuple[list[LookupType], float]]
        | list[tuple[list[LookupType | CacheObject], float]]
    ):
        return cast(
            Union[
                List[List[LookupType]],
                List[List[Union[LookupType, CacheObject]]],
                List[Tuple[List[LookupType], float]],
                List[Tuple[List[Union[LookupType, CacheObject]], float]],
            ],
            super().get_fuzzy_matches(
                name, limit=limit, cutoff=cutoff, with_cached=with_cached, with_scores=with_scores
            ),
        )

    def get_fuzzy(
        self, name: str, *, cutoff: float = 0.6, with_cached: bool = False
    ) -> list[LookupType] | list[LookupType | CacheObject] | None:
        return cast(
            Optional[Union[List[LookupType], List[Union[LookupType, CacheObject]]]],
            super().get_fuzzy(name, cutoff=cutoff, with_cached=with_cached),
        )


def chunk(list_to_chunk: list[_X], chunk_length: int) -> Generator[list[_X], None, None]:
    """
    A helper generator that divides the input list into chunks of ``chunk_length`` length.
    The last chunk may be shorter than specified.

    Parameters
    ----------
    list_to_chunk : list[X]
        The list you want to divide into chunks.
    chunk_length : int
        The length of each chunk.

    Returns
    -------
    Generator[list[X], None, None]
        A generator yielding chunks of the given length.
    """
    for i in range(0, len(list_to_chunk), chunk_length):
        yield list_to_chunk[i:i + chunk_length]


async def expand_partial(iterable: Iterable[_X]) -> AsyncGenerator[_X, None]:
    """
    A helper async generator that can be used to automatically expand partial objects for you.
    Any other object found in the ``iterable`` is passed unchanged.

    The following classes are converted:
        `PartialPlayer <arez.PartialPlayer>` -> `Player <arez.Player>`\n
        `PartialMatch <arez.PartialMatch>` -> `Match <arez.Match>`

    Parameters
    ----------
    iterable : Iterable
        The iterable containing partial objects.

    Returns
    -------
    AsyncGenerator
        An async generator yielding expanded versions of each partial object.
    """
    for element in iterable:
        if isinstance(element, Expandable):
            expanded = await element
            yield expanded
        else:
            yield element


def _int_divmod(base: int | float, div: int | float) -> tuple[int, int]:
    result = divmod(base, div)
    return (int(result[0]), int(result[1]))


class Duration:
    """
    Represents a duration. Allows for easy conversion between time units.

    This object isn't a subclass of `datetime.timedelta`, but behaves as such - it's also
    immutable, and anything you'd normally be able to do on a `datetime.timedelta` object,
    should be doable on this as well. This includes addition, substraction, multiplication,
    division (true and floor), modulo, divmod, negation and getting absolute value.
    Operations support the second argument being a normal `datetime.timedelta`,
    but the return value is always an instance of this class.
    If you prefer doing math using a normal `datetime.timedelta` object,
    you can use the `to_timedelta` method to convert it to such.
    """
    __slots__ = (
        "_delta", "_days", "_hours", "_minutes", "_seconds", "_microseconds", "_total_seconds"
    )

    def __init__(self, **kwargs):
        self._delta = timedelta(**kwargs)
        self._total_seconds = self._delta.total_seconds()
        seconds, us_fraction = divmod(self._total_seconds, 1)
        self._microseconds = round(us_fraction * 1e6)  # convert the fractional seconds
        minutes, seconds = _int_divmod(seconds, 60)
        self._seconds = seconds
        hours, minutes = _int_divmod(minutes, 60)
        self._minutes = minutes
        days, hours = _int_divmod(hours, 24)
        self._hours = hours
        self._days = days

    @property
    def days(self) -> int:
        """
        Returns days as an integer.

        Note: It is possible for this number to be negative, if it's been constructed from a
        negative `datetime.timedelta`.
        """
        return self._days

    @property
    def hours(self) -> int:
        """
        Returns hours in range 0-23.
        """
        return self._hours

    @property
    def minutes(self) -> int:
        """
        Returns minutes in range 0-59.
        """
        return self._minutes

    @property
    def seconds(self) -> int:
        """
        Returns seconds in range of 0-59.
        """
        return self._seconds

    @property
    def microseconds(self) -> int:
        """
        Returns microseconds in range 0-999999
        """
        return self._microseconds

    def total_days(self) -> float:
        """
        The total amount of days within the duration, as a `float`.
        """
        return self._total_seconds / 86400

    def total_hours(self) -> float:
        """
        The total amount of hours within the duration, as a `float`.
        """
        return self._total_seconds / 3600

    def total_minutes(self) -> float:
        """
        The total amount of minutes within the duration, as a `float`.
        """
        return self._total_seconds / 60

    def total_seconds(self) -> float:
        """
        The total amount of seconds within the duration, as a `float`.
        """
        return self._total_seconds

    def to_timedelta(self) -> timedelta:
        """
        Converts this `Duration` object into `datetime.timedelta`.
        """
        return self._delta

    @classmethod
    def from_timedelta(cls, delta: timedelta) -> Duration:
        """
        Returns a `Duration` instance constructed from a `datetime.timedelta` object.
        """
        return cls(seconds=delta.total_seconds())

    def __repr__(self) -> str:
        args: list[tuple[str, float]] = []
        if self._days:
            args.append(("days", self._days))
        if self._hours or self._minutes or self._seconds:
            args.append(("seconds", self._hours * 3600 + self._minutes * 60 + self._seconds))
        if self._microseconds:
            args.append(("microseconds", self._microseconds))
        return f"Duration({', '.join(f'{unit}={amount}' for unit, amount in args)})"

    def __str__(self) -> str:
        if self._days:
            s = 's' if abs(self._days) > 1 else ''
            days = f"{self._days} day{s}, "
        else:
            days = ''
        if self._hours:
            hours = f"{self._hours}:"
        else:
            hours = ''
        if self._microseconds:
            ms = f".{self._microseconds:06}"
        else:
            ms = ''
        return f"{days}{hours}{self._minutes:02}:{self._seconds:02}{ms}"

    def _get_delta(self, other: object) -> timedelta:
        if isinstance(other, Duration):
            return other._delta
        elif isinstance(other, timedelta):
            return other
        return NotImplemented

    # Comparisons

    def _cmp(self, opr: Callable[[object, object], bool], other: object) -> bool:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return opr(self._delta, delta)

    __eq__ = cast(Callable[[object, object], bool], partialmethod(_cmp, eq))
    __ne__ = cast(Callable[[object, object], bool], partialmethod(_cmp, ne))
    __lt__ = partialmethod(_cmp, lt)
    __le__ = partialmethod(_cmp, le)
    __gt__ = partialmethod(_cmp, gt)
    __ge__ = partialmethod(_cmp, ge)

    # Math operations

    def __add__(self, other: Duration | timedelta) -> Duration:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return Duration(seconds=self._total_seconds + delta.total_seconds())

    __radd__ = __add__

    def __sub__(self, other: Duration | timedelta) -> Duration:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return Duration(seconds=self._total_seconds - delta.total_seconds())

    def __rsub__(self, other: Duration | timedelta) -> Duration:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return Duration(seconds=delta.total_seconds() - self._total_seconds)

    def __mul__(self, other: int | float) -> Duration:
        if not isinstance(other, (int, float)):
            return NotImplemented
        return Duration(seconds=self._total_seconds * other)

    __rmul__ = __mul__

    @overload
    def __truediv__(self, other: Duration | timedelta) -> float:
        ...

    @overload
    def __truediv__(self, other: int | float) -> Duration:
        ...

    def __truediv__(self, other: Duration | timedelta | int | float):
        if isinstance(other, (int, float)):
            return Duration(seconds=self._total_seconds / other)
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return self._total_seconds / delta.total_seconds()

    def __rtruediv__(self, other: timedelta) -> float:
        if not isinstance(other, timedelta):
            return NotImplemented
        return other.total_seconds() / self._total_seconds

    @overload
    def __floordiv__(self, other: Duration | timedelta) -> int:
        ...

    @overload
    def __floordiv__(self, other: int) -> Duration:
        ...

    def __floordiv__(self, other: Duration | timedelta | int):
        if isinstance(other, int):
            return Duration(microseconds=floor(self._total_seconds * 1e6 // other))
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return int(self._total_seconds // delta.total_seconds())

    def __rfloordiv__(self, other: timedelta) -> int:
        if not isinstance(other, timedelta):
            return NotImplemented
        return int(other.total_seconds() // self._total_seconds)

    def __mod__(self, other: Duration | timedelta) -> Duration:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return Duration(seconds=(self._total_seconds % delta.total_seconds()))

    def __rmod__(self, other: Duration | timedelta) -> Duration:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        return Duration(seconds=(delta.total_seconds() % self._total_seconds))

    def __divmod__(self, other: Duration | timedelta) -> tuple[int, Duration]:
        if (delta := self._get_delta(other)) is NotImplemented:
            return NotImplemented
        q, r = divmod(self._total_seconds, delta.total_seconds())
        return (int(q), Duration(seconds=r))

    def __rdivmod__(self, other: timedelta) -> tuple[int, Duration]:
        if not isinstance(other, timedelta):
            return NotImplemented
        q, r = divmod(other.total_seconds(), self._total_seconds)
        return (int(q), Duration(seconds=r))

    def __pos__(self):
        return Duration(seconds=self._total_seconds)

    def __neg__(self):
        return Duration(seconds=-self._total_seconds)

    def __abs__(self):
        if self._total_seconds < 0:
            return Duration(seconds=-self._total_seconds)
        return Duration(seconds=self._total_seconds)


class WeakValueDefaultDict(WeakValueDictionary, Mapping[_X, _Y]):  # type: ignore[type-arg]
    def __init__(
        self,
        default_factory: Callable[[], Any] | None = None,
        mapping_or_iterable: Mapping[_X, _Y] | Iterable[tuple[_X, _Y]] = {},
    ):
        self.default_factory = default_factory
        super().__init__(mapping_or_iterable)

    def __getitem__(self, key: _X) -> _Y:
        try:
            return super().__getitem__(key)
        except KeyError:
            if not self.default_factory:  # pragma: no cover
                raise
            item = self.default_factory()
            self.__setitem__(key, item)
            return item


class CacheDict(Dict[_X, _Y]):
    def __init__(self, value_factory: Callable[[_X], _Y], *args, **kwargs):
        self._value_factory = value_factory
        super().__init__(*args, **kwargs)

    def __missing__(self, key: _X) -> _Y:
        value = self._value_factory(key)
        super().__setitem__(key, value)
        return value
