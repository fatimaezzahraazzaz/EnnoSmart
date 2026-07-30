import psycopg2

DB = {
    "dbname": "ennosmart",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5432,
}

conn = psycopg2.connect(**DB)
cur = conn.cursor()

cur.execute("""
SELECT
    COUNT(*) AS total_documents,
    COUNT(file_data) AS documents_en_base,
    COUNT(*) - COUNT(file_data) AS documents_sans_binaire
FROM documents;
""")

print("=== Résumé global ===")
print(cur.fetchone())

cur.execute("""
SELECT id, project_id, filename, file_size, storage_mode, file_sha256
FROM documents
WHERE project_id = 4
ORDER BY id;
""")

print("\n=== Projet 4 ===")
for row in cur.fetchall():
    print(row)

cur.close()
conn.close()
