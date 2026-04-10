# dbclear.py
# Deletes all rows from the 'users' table in main.db
import sqlite3

DB_PATH = "storage/main.db"

def clear_users_table(db_path):
	conn = sqlite3.connect(db_path)
	cursor = conn.cursor()
	cursor.execute("DELETE FROM users")
	conn.commit()
	conn.close()
	print("All records deleted from 'users' table.")

if __name__ == "__main__":
	clear_users_table(DB_PATH)
