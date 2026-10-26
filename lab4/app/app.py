import os
import threading
import time

import psycopg
from flask import Flask, redirect, render_template_string, request

PRIMARY_DSN = os.environ["PRIMARY_DSN"]   # сюда пишем
REPLICA_DSN = os.environ["REPLICA_DSN"]   # отсюда читаем

app = Flask(__name__)
load_state = {"running": False, "batches": 0}


def run(dsn, sql, params=None, fetch=False):
    with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if fetch else None


def init_db():
    for _ in range(30):
        try:
            run(PRIMARY_DSN, """
                CREATE TABLE IF NOT EXISTS notes (
                    id         serial PRIMARY KEY,
                    text       text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )""")
            return
        except Exception as e:
            print("БД ещё не готова:", e, flush=True)
            time.sleep(2)


def load_worker():
     # 100 пачек по 2000 строк, каждая пачка — отдельная транзакция
    try:
        for i in range(100):
            run(PRIMARY_DSN, """
                INSERT INTO notes (text)
                SELECT 'нагрузка ' || g || ' ' || repeat('x', 500)
                FROM generate_series(1, 2000) g""")
            load_state["batches"] = i + 1
    except Exception as e:
        print("Ошибка нагрузки:", e, flush=True)
    finally:
        load_state["running"] = False


PAGE = """
<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
{% if auto %}<meta http-equiv="refresh" content="1">{% endif %}
<title>Записи</title>
<style>
 body { font-family: sans-serif; max-width: 800px; margin: 30px auto; }
 .box { padding: 10px; border: 1px solid #ccc; margin: 10px 0; }
 .err { color: #b00; } .ok { color: #070; }
 td, th { padding: 3px 8px; text-align: left; }
</style></head><body>
<h1>Записи</h1>

<form method="post" action="/create" style="display:inline">
  <input name="text" placeholder="текст записи">
  <button>Создать запись</button>
</form>
<form method="post" action="/load" style="display:inline">
  <button>Налить нагрузку</button>
</form>
<a href="/?auto={{ 0 if auto else 1 }}">{{ "Выключить" if auto else "Включить" }} автообновление</a>

{% if msg %}<p class="{{ 'err' if 'Ошибка' in msg else 'ok' }}">{{ msg }}</p>{% endif %}

<div class="box">
  Записей на <b>primary</b>: {{ primary_count }}<br>
  Записей на <b>реплике</b>: {{ replica_count }}<br>
  Нагрузка: {{ "идёт, пачек " ~ load.batches ~ "/100" if load.running else "не идёт" }}
</div>

<h2>Последние записи (читаем с реплики)</h2>
{% if replica_error %}<p class="err">Ошибка чтения с реплики: {{ replica_error }}</p>{% endif %}
<table>
  <tr><th>id</th><th>текст</th><th>создана</th></tr>
  {% for r in rows %}
  <tr><td>{{ r[0] }}</td><td>{{ r[1][:40] }}</td><td>{{ r[2].strftime('%H:%M:%S') }}</td></tr>
  {% endfor %}
</table>
</body></html>
"""


@app.get("/")
def index():
    rows, replica_count, replica_error = [], "—", None
    try:
        rows = run(REPLICA_DSN,
                   "SELECT id, text, created_at FROM notes ORDER BY id DESC LIMIT 15",
                   fetch=True)
        replica_count = run(REPLICA_DSN, "SELECT count(*) FROM notes", fetch=True)[0][0]
    except Exception as e:
        replica_error = str(e).strip()

    try:
        primary_count = run(PRIMARY_DSN, "SELECT count(*) FROM notes", fetch=True)[0][0]
    except Exception:
        primary_count = "недоступен"

    return render_template_string(
        PAGE, rows=rows, replica_count=replica_count, replica_error=replica_error,
        primary_count=primary_count, load=load_state,
        msg=request.args.get("msg"), auto=request.args.get("auto") == "1")


@app.post("/create")
def create():
    text = request.form.get("text") or "запись"
    try:
        run(PRIMARY_DSN, "INSERT INTO notes (text) VALUES (%s)", (text,))
        msg = "Запись создана в primary"
    except Exception as e:
        msg = f"Ошибка записи в primary: {str(e).strip()}"
    return redirect(f"/?msg={msg}")


@app.post("/load")
def load():
    if not load_state["running"]:
        load_state.update(running=True, batches=0)
        threading.Thread(target=load_worker, daemon=True).start()
    return redirect("/?auto=1")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, threaded=True)
