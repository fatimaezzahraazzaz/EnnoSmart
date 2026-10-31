"""V4 table parser; HTML tables whose caption V4 misses get their wrapper's literal label/caption."""
from ..reuse import load
from .html_floats import TABLE_LABEL, base_caption_found, describe, table_container
base = load("tools._tables_base", "tools/tables.py")
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)


def html_table(table):
    result = base.html_table(table)
    # V4 output is kept whenever V4 found a caption, and JATS keeps its own path.
    if result.get("caption") or base_caption_found(table):
        return result
    container = table_container(table)
    if container is None:
        return result
    info = describe(container, TABLE_LABEL, table)
    derived = base.structure(result["headers"], result["rows"], info["caption"])
    result.update(caption=info["caption"], number=info["number"], label=info["label"],
        element_id=result.get("element_id") or info["id"],
        dataset=derived["dataset"], metric_labels=derived["metric_labels"], unit=derived["unit"])
    return result


def table_text(table):
    """V4 text; a separate label is prefixed exactly as the JATS path already does."""
    value = base.table_text(table)
    label = table.get("label")
    return label+"\n"+value if label and not value.startswith(label) else value
