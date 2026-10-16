"""Library presentation follows recorded judgments; never infer relevance from collection."""


def library_groups(state, primary=()):
    primary = set(primary)
    examined = set(state.get("examined_article_ids", []))
    held = set(state.get("held_article_ids", []))
    groups = {key: [] for key in ("relevant", "retained_complementary", "unexamined", "examined_pending", "rejected", "on_hold")}
    for aid, article in state.get("articles", {}).items():
        decision = article.get("assessment", {}).get("decision")
        if aid in held and aid not in primary:
            # Put on hold by the consultant: waiting, neither selected nor rejected.
            groups["on_hold"].append(aid)
            continue
        if article.get("selection_override") is False and aid not in primary:
            groups["rejected"].append(aid)
            continue
        if decision == "retain" or article.get("selection_override") is True:
            groups["relevant"].append(aid)
        if aid in primary:
            continue
        if decision == "retain" or article.get("selection_override") is True:
            groups["retained_complementary"].append(aid)
        elif decision == "reject":
            groups["rejected"].append(aid)
        elif aid in examined or decision == "pending":
            groups["examined_pending"].append(aid)
        else:
            groups["unexamined"].append(aid)
    return groups
