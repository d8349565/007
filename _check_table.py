from app.models.db import get_connection
c = get_connection()
rows = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print([r[0] for r in rows])
c.close()
