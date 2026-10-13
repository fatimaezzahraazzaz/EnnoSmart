"""Library presentation follows recorded judgments; never infer relevance from collection."""


def library_groups(state, primary=()):
    primary = set(primary)
    examined = set(state.get("examined_article_ids", []))
    groups = {key: [] for key in ("relevant", "retained_complementary", "unexamined", "examined_pending", "rejected")}
    for aid, article in state.get("articles", {}).items():
        decision = article.get("assessment", {}).get("decision")
        if decision == "retain":
            groups["relevant"].append(aid)
        if aid in primary:
            continue
        if decision == "retain":
            groups["retained_complementary"].append(aid)
        elif decision == "reject":
            groups["rejected"].append(aid)
        elif aid in examined or decision == "pending":
            groups["examined_pending"].append(aid)
        else:
            groups["unexamined"].append(aid)
    return groups
