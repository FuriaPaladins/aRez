import arez
import pytest

from .conftest import MATCH


# test type errors
@pytest.mark.base()
@pytest.mark.asyncio()
async def test_type_errors(api: arez.PaladinsAPI):
    # string as platform
    with pytest.raises(TypeError):
        await api.search_players("1234", "pc")  # type: ignore


# test enum creation and casting
@pytest.mark.base()
@pytest.mark.dependency()
@pytest.mark.dependency(scope="session")
def test_enum():
    p = arez.Platform("steam")  # fuzzy string member getting
    assert p is arez.Platform.Steam  # identity and attribute access
    assert str(p) == "Steam"  # str cast
    assert int(p) == 5  # int cast
    assert repr(p) == "<Steam: 5>"  # repr
    # member acquisition by value
    l = arez.Language(2)
    assert l is arez.Language.German
    # None for unknown input
    r = arez.Region("1234")
    assert r is None
    # Default for unknown input
    r = arez.Region("1234", return_default=True)
    assert r is arez.Region.Unknown
    # Default, if no default value is set - return unchanged
    l = arez.Language("1234", return_default=True)
    assert l == "1234"
    # simple comparison
    assert r != None  # noqa


@pytest.mark.api()
@pytest.mark.vcr()
@pytest.mark.asyncio()
@pytest.mark.dependency(depends=["tests/test_endpoint.py::test_session"], scope="session")
async def test_get_server_status(api: arez.PaladinsAPI):
    # test Notfound
    with pytest.raises(arez.NotFound):
        current_status = await api.get_server_status()
    # test fetching first, limited, not all up
    current_status = await api.get_server_status()
    assert isinstance(current_status, arez.ServerStatus)
    assert not current_status.all_up
    assert current_status.limited_access
    # repr with limited access
    assert len(current_status.statuses) > 0
    repr(current_status)
    repr(current_status.statuses[0])
    # test returning cached
    current_status2 = await api.get_server_status()
    assert current_status2 is current_status
    # test force refresh
    current_status = await api.get_server_status(force_refresh=True)
    assert current_status2 is not current_status
    # test attributes
    assert len(current_status.statuses) == 5
    assert hasattr(current_status, "pc")
    assert hasattr(current_status, "ps4")
    assert hasattr(current_status, "pts")
    assert hasattr(current_status, "xbox")
    assert hasattr(current_status, "switch")
    # repr
    repr(current_status)
    repr(current_status.statuses[0])


@pytest.mark.api()
@pytest.mark.vcr()
@pytest.mark.base()
@pytest.mark.asyncio()
@pytest.mark.dependency(depends=["test_enum"])
@pytest.mark.dependency(depends=["tests/test_endpoint.py::test_session"], scope="session")
async def test_cache(api: arez.PaladinsAPI):
    # set default language
    api.set_default_language(arez.Language.English)
    # fail initialize
    result = await api.initialize()
    assert result is False
    # proper initialize
    result = await api.initialize(language=arez.Language.English)
    assert result is True
    # getting entry
    entry = api.get_entry()
    assert isinstance(entry, arez.CacheEntry)
    # repr
    repr(entry)
    # get a valid champion, then an invalid card and talent
    champion = entry.get_champion("Androxus")
    assert champion is not None
    # repr Champion and Ability
    repr(champion)
    repr(list(champion.abilities)[0])
    assert champion.get_card(0) is None
    assert champion.get_talent(0) is None
    # fail getting a champion, talent, card, shop item and device, due to invalid ID
    assert api.get_champion(0) is None
    assert api.get_talent(0) is None
    assert api.get_card(0) is None
    assert api.get_item(0) is None
    assert api.get_device(0) is None
    # get specific entry - fail cos missing initialize
    german = arez.Language.German
    entry = api.get_entry(german)
    assert entry is None
    # fail getting a champion, talent, card, shop item and device, due to missing cache
    assert api.get_champion(0, language=german) is None
    assert api.get_talent(0, language=german) is None
    assert api.get_card(0, language=german) is None
    assert api.get_item(0, language=german) is None
    assert api.get_device(0, language=german) is None


@pytest.mark.api()
@pytest.mark.vcr()
@pytest.mark.base()
@pytest.mark.asyncio()
@pytest.mark.dependency(depends=["test_cache"])
@pytest.mark.dependency(
    depends=[
        "tests/test_api.py::test_get_match",
        "tests/test_match.py::test_live_match",
        "tests/test_player.py::test_player_history",
        "tests/test_player.py::test_player_loadouts",
        "tests/test_player.py::test_player_champion_stats",
    ],
    scope="session",
)
async def test_cache_disabled(api: arez.PaladinsAPI, player: arez.Player):
    # temporarly disable the cache, and make sure no cached entry exists
    if arez.Language.English in api._cache:
        del api._cache[arez.Language.English]  # delete cache
    api.cache_enabled = False  # disable cache

    try:
        # test get_match
        match = await api.get_match(MATCH)
        assert isinstance(match, arez.Match)
        # test live players
        status = await player.get_status()
        live_match = await status.get_live_match()
        assert isinstance(live_match, arez.LiveMatch)
        # test player history
        history = await player.get_match_history()
        assert all(isinstance(match, arez.PartialMatch) for match in history)
        # repr CacheObject
        if len(history) > 0:
            repr(history[0].champion)
        # test player loadouts
        loadouts = await player.get_loadouts()
        assert all(isinstance(l, arez.Loadout) for l in loadouts)
        # test player champion stats
        stats_list = await player.get_champion_stats()
        assert all(isinstance(l, arez.ChampionStats) for l in stats_list)
    finally:
        # finalize
        player._api.cache_enabled = True  # enable cache back
        await api.initialize()  # re-fetch the entry


@pytest.mark.api()
@pytest.mark.vcr()
@pytest.mark.base()
@pytest.mark.match()
@pytest.mark.player()
@pytest.mark.asyncio()
@pytest.mark.dependency(depends=["test_cache"])
@pytest.mark.dependency(depends=["tests/test_player.py::test_player_history"], scope="session")
async def test_comparisons(
    api: arez.PaladinsAPI, player: arez.PartialPlayer, private_player: arez.PartialPlayer
):
    # players
    assert player != private_player
    # champions
    entry = api.get_entry()
    assert entry is not None
    champions = list(entry.champions)
    assert champions[0] != champions[1]
    # devices
    devices = list(entry.devices)
    assert devices[0] != devices[1]

    history = await player.get_match_history()
    # loop because the last match might have only one item in it
    for partial_match in history:
        items = partial_match.items
        cards = partial_match.loadout.cards
        if len(items) >= 2 and len(cards) >= 2:
            break
    # match item
    assert items[0] != items[1]
    # NotImplemented
    assert items[0] != None  # noqa
    # loadout card
    assert cards[0] != cards[1]
    # NotImplemented
    assert cards[0] != None  # noqa


@pytest.mark.vcr()
@pytest.mark.player()
@pytest.mark.asyncio()
@pytest.mark.dependency(depends=["tests/test_player.py::test_player_expand"], scope="session")
async def test_player_ranked_best(player: arez.PartialPlayer):
    player1 = await player
    assert isinstance(player1.ranked_best, arez.RankedStats)
    player2 = await player
    assert isinstance(player2.ranked_best, arez.RankedStats)
