from __future__ import annotations

import re
from typing import TYPE_CHECKING
from datetime import datetime, timedelta, timezone

import arez
import pytest

from .conftest import (
    BASE_DATETIME,
    MATCH,
    MATCH_TDM,
    INVALID_MATCH,
    PLAYER,
    OLD_PLAYER,
    CONSOLE_PLAYER,
    PRIVATE_PLAYER,
    INVALID_PLAYER,
    PLATFORM_PLAYER,
    INVALID_PLATFORM,
)

if TYPE_CHECKING:
    from _pytest.fixtures import SubRequest


pytestmark = [
    pytest.mark.api,
    pytest.mark.vcr,
    pytest.mark.asyncio,
    pytest.mark.order(after=["test_misc.py::test_enum", "test_misc.py::test_cache"])
]


class TestChampionInfo:
    _name_pattern = re.compile(r'test_champion_info\[(\d{1,2})-(False|True|None)\]')

    # we overwrite this here, to provide custom cassette naming
    # we reduce the parametrization to just '[N]' instead of '[N-(False|True|None)],
    # mostly just to reuse the english language cassette
    @pytest.fixture()
    def default_cassette_name(self, request: SubRequest) -> str:
        return self._name_pattern.sub(r"test_champion_info[\1]", request.node.name)

    @pytest.mark.slow()
    @pytest.mark.order(after="utils/test_lookup.py::test_lookup")
    @pytest.mark.parametrize(["lang_num", "cache"], [
        (0, False),   # Nothing returned
        (1, None),    # English - don't pass anything
        (1, False),   # English - don't cache
        (1, True),    # English - do cache
        (2, False),   # German
        (3, False),   # French
        pytest.param(5, False, marks=pytest.mark.xfail),  # Chinese
        (9, False),   # Spanish
        (10, False),  # Portuguese
        (11, False),  # Russian
        (12, False),  # Polish
        (13, False),  # Turkish
    ])
    async def test_champion_info(self, api: arez.PaladinsAPI, lang_num: int, cache: bool):
        if lang_num == 0:
            # hack - ensure no cache exists
            if arez.Language.English in api._cache:
                del api._cache[arez.Language.English]
            # nothing is returned, expect an exception
            with pytest.raises(arez.NotFound):
                champion_info = await api.get_champion_info()
            # check TypeError
            with pytest.raises(TypeError):
                champion_info = await api.get_champion_info("test")  # type: ignore
            return  # if the above passes, end here
        champion_info = await api.get_champion_info(arez.Language(lang_num), cache=cache)
        assert champion_info is not None
        champion_count = len(champion_info.champions)
        # repr
        repr(champion_info)
        # verify all 3 device categories separately
        assert len(champion_info.items) == 4 * 4, "Missing shop items!"
        try:
            assert len(champion_info.cards) == champion_count * 16, "Missing cards!"
        except AssertionError:
            # there is a chance only one champion is missing those - narrow it down
            if len(champion_info.cards) != (champion_count - 1) * 16:
                raise
            # lets see who is it
            for champion in champion_info.champions:
                assert champion.cards, f"Champion {champion.name} is missing cards!"
        try:
            assert len(champion_info.talents) == champion_count * 3, "Missing talents!"
        except AssertionError:
            if len(champion_info.talents) != (champion_count - 1) * 3:
                raise
            for champion in champion_info.champions:
                assert champion.talents, f"Champion {champion.name} is missing talents!"
        # verify we have all devices:
        # • 16 cards per champion
        # • 3 talents per champion
        # 4 categories of 4 shop items
        assert len(champion_info.devices) == champion_count * (16 + 3) + 4 * 4, "Missing devices!"
        # verify all champions are valid
        assert all(c for c in champion_info.champions), "Not all champions appear to be valid!"


async def test_get_player(api: arez.PaladinsAPI):
    # standard - name
    player = await api.get_player(PLAYER.name)
    assert isinstance(player, arez.Player)
    assert player.id == PLAYER.id
    assert player.name == PLAYER.name
    assert player.platform.value == PLAYER.platform
    # repr Player and Stats
    repr(player)
    repr(player.casual)
    # standard - ID
    player = await api.get_player(PLAYER.id)
    assert isinstance(player, arez.Player)
    # console - ID
    player = await api.get_player(CONSOLE_PLAYER.id)
    assert isinstance(player, arez.Player)
    # private
    with pytest.raises(arez.Private):
        player = await api.get_player(PRIVATE_PLAYER.id)
    # return private
    private_player = await api.get_player(PRIVATE_PLAYER.id, return_private=True)
    assert isinstance(private_player, arez.PartialPlayer)
    # repr
    repr(private_player)
    # zero
    with pytest.raises(arez.NotFound):
        player = await api.get_player(0)
    # old
    old_player = await api.get_player(OLD_PLAYER.id)
    assert old_player.created_at is None
    assert old_player.last_login is None
    assert old_player.region is arez.Region.Unknown
    # not found
    with pytest.raises(arez.NotFound):
        player = await api.get_player(INVALID_PLAYER.id)


async def test_get_players(api: arez.PaladinsAPI):
    # standard
    player_list = await api.get_players([PLAYER.id, PRIVATE_PLAYER.id])
    assert len(player_list) == 1 and isinstance(player_list[0], arez.Player)
    # zero and empty list
    player_list = await api.get_players([0])
    assert len(player_list) == 0
    # return private
    private_player_list = await api.get_players(
        [PLAYER.id, PRIVATE_PLAYER.id], return_private=True
    )
    assert (
        len(private_player_list) == 2
        and isinstance(private_player_list[0], arez.Player)
        and isinstance(private_player_list[1], arez.PartialPlayer)
    )


async def test_search_players(api: arez.PaladinsAPI):
    # all platforms
    player_list = await api.search_players(PLAYER.name)
    assert len(player_list) == 2 and isinstance(player_list[0], arez.PartialPlayer)
    # specific PC platform
    player_list = await api.search_players(PLAYER.name, arez.Platform(PLAYER.platform))
    assert len(player_list) == 1 and isinstance(player_list[0], arez.PartialPlayer)
    # specific console platform
    player_list = await api.search_players(
        CONSOLE_PLAYER.name, arez.Platform(CONSOLE_PLAYER.platform)
    )
    assert len(player_list) == 1 and isinstance(player_list[0], arez.PartialPlayer)
    # non-exact names, all platforms
    player_list = await api.search_players(PLAYER.name, exact=False)
    assert len(player_list) >= 2 and all(isinstance(p, arez.PartialPlayer) for p in player_list)
    # non-exact names, PC platform
    player_list = await api.search_players(
        PLAYER.name, arez.Platform(PLAYER.platform), exact=False
    )
    assert len(player_list) >= 1 and all(isinstance(p, arez.PartialPlayer) for p in player_list)
    # return private accounts
    player_list = await api.search_players(
        PRIVATE_PLAYER.name, arez.Platform(PRIVATE_PLAYER.platform)
    )
    assert (
        len(player_list) == 1
        and isinstance(player_list[0], arez.PartialPlayer)
        and player_list[0].private
    )
    # omit private accounts - not found
    with pytest.raises(arez.NotFound):
        player_list = await api.search_players(
            PRIVATE_PLAYER.name, arez.Platform(PRIVATE_PLAYER.platform), return_private=False
        )
    # invalid player - not found
    with pytest.raises(arez.NotFound):
        player_list = await api.search_players(
            INVALID_PLAYER.name, arez.Platform(INVALID_PLAYER.platform)
        )


async def test_get_from_platform(api: arez.PaladinsAPI):
    # existing
    player = await api.get_from_platform(
        PLATFORM_PLAYER.platform_id, arez.Platform(PLATFORM_PLAYER.platform)
    )
    assert isinstance(player, arez.PartialPlayer)
    # not found
    with pytest.raises(arez.NotFound):
        player = await api.get_from_platform(
            INVALID_PLATFORM.platform_id, arez.Platform(INVALID_PLATFORM.platform)
        )


async def test_get_match(api: arez.PaladinsAPI):
    # standard
    match = await api.get_match(MATCH)
    assert isinstance(match, arez.Match)
    assert all(isinstance(mp.player, arez.PartialPlayer) for mp in match.players)
    # repr Match and MatchPlayer
    repr(match)
    repr(list(match.players)[0])
    # explicit language
    match = await api.get_match(MATCH, language=arez.Language.English)
    assert isinstance(match, arez.Match)
    # expand players after fetch
    await match.expand_players()
    assert all(isinstance(mp.player, arez.Player) or mp.player.private for mp in match.players)
    # expand players on fetch
    match = await api.get_match(MATCH, expand_players=True)
    assert isinstance(match, arez.Match)
    assert all(isinstance(mp.player, arez.Player) or mp.player.private for mp in match.players)
    # not found
    with pytest.raises(arez.NotFound):
        match = await api.get_match(INVALID_MATCH)


async def test_get_matches(api: arez.PaladinsAPI):
    # standard
    match_list = await api.get_matches([MATCH, MATCH_TDM])
    for match in match_list:
        assert isinstance(match, arez.Match)
        assert all(isinstance(mp.player, arez.PartialPlayer) for mp in match.players)
    # explicit language
    match_list = await api.get_matches([MATCH, MATCH_TDM], language=arez.Language.English)
    assert len(match_list) == 2
    # expand players
    match_list = await api.get_matches([MATCH, MATCH_TDM], expand_players=True)
    for match in match_list:
        assert isinstance(match, arez.Match)
        assert all(isinstance(mp.player, arez.Player) or mp.player.private for mp in match.players)
    # empty list
    match_list = await api.get_matches([])
    assert len(match_list) == 0


@pytest.mark.slow()
@pytest.mark.order(after="utils/test_date_gen.py::test_date_gen")
async def test_get_matches_for_queue(api: arez.PaladinsAPI):
    queue = arez.Queue.Casual_Siege
    start = BASE_DATETIME + timedelta(minutes=2)
    end = BASE_DATETIME + timedelta(minutes=6)
    prev = datetime.min
    num = 0
    # normal players, explicit language
    async for match in api.get_matches_for_queue(
        queue, language=arez.Language.English, start=start, end=end
    ):
        stamp = match.timestamp
        assert start <= stamp <= end
        assert stamp >= prev
        prev = stamp
        num += 1
        if num > 15:
            break
    assert num >= 2, "Generator needs to yield at least 2 matches!"
    # expand players, default language, reverse
    start = BASE_DATETIME + timedelta(minutes=4)
    end = BASE_DATETIME + timedelta(minutes=8)
    prev = datetime.max
    num = 0
    async for match in api.get_matches_for_queue(
        queue, start=start, end=end, reverse=True, expand_players=True
    ):
        stamp = match.timestamp
        assert start <= stamp <= end
        assert stamp <= prev
        prev = stamp
        num += 1
        if num > 15:
            break
    assert num >= 2, "Generator needs to yield at least 2 matches!"
    # local time and early exit
    start = BASE_DATETIME.replace(tzinfo=timezone.utc)
    end = (BASE_DATETIME - timedelta(seconds=1)).replace(tzinfo=timezone.utc)
    async for match in api.get_matches_for_queue(queue, start=start, end=end, local_time=True):
        assert False, "Generator didn't exit early!"


async def test_bounty(api: arez.PaladinsAPI):
    # explicit language and empty response
    with pytest.raises(arez.NotFound):
        active_items, past_items = await api.get_bounty(
            language=arez.Language.English
        )
    # normal
    active_items, past_items = await api.get_bounty()
    assert all(isinstance(item, arez.BountyItem) for item in active_items)
    assert all(isinstance(item, arez.BountyItem) for item in past_items)
