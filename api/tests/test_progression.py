from locomission.progression import AVATAR_IDS, achievements


def test_achievements_are_derived_without_storing_private_progress():
    profile = {"completed_missions": 5}
    values = {a["id"]: a for a in achievements(profile, groups_joined=1)}
    assert values["first"]["unlocked"]
    assert values["five"]["unlocked"]
    assert not values["twenty"]["unlocked"]
    assert values["twenty"]["current"] == 5
    assert values["team"]["unlocked"]
    assert len(set(AVATAR_IDS)) == 5


def test_achievement_progress_is_clamped_to_goal():
    values = achievements({"completed_missions": 25}, groups_joined=3)
    assert all(item["unlocked"] for item in values)
    assert all(item["current"] == item["goal"] for item in values)
