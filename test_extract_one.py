import faulthandler
import sys
import traceback

faulthandler.enable()

path = sys.argv[1]

print("=" * 90, flush=True)
print("TEST :", path, flush=True)
print("=" * 90, flush=True)

try:
    from modules.NLP.document_loader import load_documents

    documents = load_documents(
        [path],
        use_ennosmart_extraction=True,
        include_cir_final=False,
    )

    print(f"EXTRACTION_OK documents={len(documents)}", flush=True)

    for index, document in enumerate(documents, start=1):
        if isinstance(document, dict):
            text = str(
                document.get("text")
                or document.get("content")
                or document.get("page_content")
                or ""
            )
        else:
            text = str(
                getattr(document, "page_content", "")
                or getattr(document, "text", "")
                or document
            )

        print(
            f"DOCUMENT_{index}: caracteres={len(text)}",
            flush=True,
        )

except Exception:
    traceback.print_exc()
    sys.exit(1)
