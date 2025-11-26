# Minimal stub for pandas used only for read_sql_query in this project

def read_sql_query(query, conn):
    cur = conn.cursor()
    cur.execute(query)
    return cur.fetchall()
