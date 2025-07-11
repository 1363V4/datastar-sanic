from sanic import Sanic, html
from sanic.log import logger, LOGGING_CONFIG_DEFAULTS

from datastar_py import ServerSentEventGenerator as SSE
from datastar_py.sanic import datastar_respond, DatastarResponse

from tinydb import TinyDB, where

import re
import time
import uuid
import asyncio
from collections import defaultdict
from random import choice
from datetime import datetime, timedelta
from pprint import pprint


LOGGING_CONFIG_DEFAULTS['formatters']['simple'] = {
    'format': '%(asctime)s - (%(name)s)[%(levelname)s]: %(message)s',
    'datefmt': '%Y-%m-%d %H:%M:%S'
}
LOGGING_CONFIG_DEFAULTS['handlers']['perso'] = {
    'class': 'logging.FileHandler',
    'formatter': 'simple',
    'filename': "/home/le/crazystar/perso.log"
}
LOGGING_CONFIG_DEFAULTS['loggers']['sanic.root']['handlers'] = ['perso']

app = Sanic(__name__, log_config=LOGGING_CONFIG_DEFAULTS)
app.static('/static', './static')

logger.info("App started")

async def cleanup_old_rooms():
    while True:
        one_day_ago = datetime.now() - timedelta(days=1)
        rooms_to_remove = []
        for room in app.ctx.rooms.all():
            room_time_str = room['time']
            room_datetime = datetime.strptime(room_time_str, "%a %b %d %H:%M:%S %Y")
            if room_datetime < one_day_ago:
                rooms_to_remove.append(room['name'])
        for room_name in rooms_to_remove:
            app.ctx.rooms.remove(where('name') == room_name)
            if room_name in app.ctx.connections:
                del app.ctx.connections[room_name]
        await asyncio.sleep(24 * 60 * 60) # daily

app.add_task(cleanup_old_rooms)

async def index_view():
    data = app.ctx.rooms.all()[::-1]
    rooms_html = ''.join(f"<a class='button' href='/room/{room['name']}'>{room['name']}</a>" for room in data)
    return f'''
<main id="main" class="gf10v gc">
    <img id="cover" src="/static/img/cover.png">
    <div class="gc" data-signals-creating__ifmissing="0">
        <button data-show="!$creating" data-on-click="$creating = !$creating">Create room</button>
        <form data-on-submit="$creating = 0; @post('/create')">
            <input data-bind-room_name type="text" name="room_name" data-show="$creating" placeholder="room name" required></input>
        </form>
        <p>Join room:</p>
        {rooms_html}
        <p data-on-click="@post('/loc/fr')">fr</p>
        <p data-on-click="@post('/loc/en')">en</p>
    </div>
</main>
'''

async def room_view(room_name, power, admin, loc):
    if power == "unknown":
        return f'''
<main id="main" class="gc">
    <img id="cover" src="/static/img/unknown.png">
    {f'''<button data-on-click="@post('/{room_name}/reveal')">Reveal</button>''' if admin else '''<p>Get ready...</p>'''}
</main>
    '''
    else:
        power_data = app.ctx.powers.search(where('id') == power)[0]
        return f'''
<main id="main" class="gc">
    <h2 class="gt l">{power_data['name'][loc]}</h2>
    <img id="cover" src="/static/img/{power}.jpg">
    <p>{power_data['desc'][loc]}
    {"<span>O</span>" if power_data['tpm'] else ""}</p>
    {f'''<button data-on-click="@post('/{room_name}/reveal')">New game</button>''' if admin else ""}
</main>
'''

# APP
@app.before_server_start
async def attach_db(app):
    db = TinyDB('data.json', indent=4)
    powers = db.table('powers')
    rooms = db.table('rooms')
    app.ctx.rooms = rooms
    app.ctx.powers = powers
    app.ctx.connections = defaultdict(lambda: defaultdict(asyncio.Queue))


@app.on_response
async def cookie(request, response):
    if not request.cookies.get("user_id"):
        user_id = uuid.uuid4().hex
        response.add_cookie('user_id', user_id)
    if not request.cookies.get("loc"):
        response.add_cookie('loc', "fr")

@app.get("/")
async def index(request):
    return html('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>crazystar</title>
    <link rel="icon" href="/static/img/unknown.png">
    <link rel="stylesheet" href="/static/css/main.css">
    <script type="module" src="/static/js/datastar.js"></script>
</head>
<body>
<main id="main" data-on-load="@get('/index_cqrs')"></main>
</body>
</html>
''')

@app.get("/index_cqrs")
async def index_cqrs(request):
    response = await datastar_respond(request)
    user_id = request.cookies.get("user_id")
    await app.ctx.connections['index'][user_id].put('init')
    while True:
        try:
            await app.ctx.connections['index'][user_id].get()
            view_html = await index_view()
            await response.send(SSE.merge_fragments(view_html))
        except asyncio.CancelledError:
            del app.ctx.connections['index'][user_id]
            break

@app.post("/create")
async def create(request):
    signals = request.json
    if name := signals.get('room_name'):
        if not re.match(r'^[a-zA-Z]{1,10}$', name) and name != "room":
            return DatastarResponse(SSE.execute_script("alert('no funny names!!1')"))
        user_id = request.cookies.get("user_id")
        app.ctx.rooms.insert({
            'name': name,
            'time': time.ctime(),
            'admin': user_id,
            'players': {user_id: 'unknown'}
        })
        logger.info(f"Room created: {name}")
        for user_id in app.ctx.connections['index']:
            await app.ctx.connections['index'][user_id].put('new room')
    return DatastarResponse()

@app.post("/loc/<loc>")
async def set_loc(request, loc):
    response = await datastar_respond(request)
    response.add_cookie('loc', loc)
    return await response.eof()

@app.get("/room/<room_name>")
async def room(request, room_name):
    return html(f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>crazystar</title>
    <link rel="icon" href="/static/img/unknown.png">
    <link rel="stylesheet" href="/static/css/main.css">
    <script type="module" src="/static/js/datastar.js"></script>
</head>
<body>
<main id="main" class="gf10v gc" data-on-load="@get('/room/{room_name}/cqrs')"></main>
</body>
</html>
''')

@app.get("/room/<room_name>/cqrs")
async def room_cqrs(request, room_name):
    response = await datastar_respond(request)
    user_id = request.cookies.get("user_id")
    loc = request.cookies.get("loc")
    room = app.ctx.rooms.get(where("name") == room_name)
    if user_id not in room['players']:
        players = room['players']
        players.update({user_id: 'unknown'})
        app.ctx.rooms.update({'players': players}, where("name") == room_name)
    await app.ctx.connections[room_name][user_id].put('init')
    while True:
        try:
            await app.ctx.connections[room_name][user_id].get()
            room = app.ctx.rooms.get(where("name") == room_name)
            power = room['players'][user_id]
            view_html = await room_view(room_name, power, room['admin'] == user_id, loc)
            await response.send(SSE.merge_fragments(view_html, use_view_transition=True))
        except asyncio.CancelledError:
            del app.ctx.connections[room_name][user_id]
            break

@app.post("/<room_name>/reveal")
async def reveal(request, room_name):
    room = app.ctx.rooms.get(where("name") == room_name)
    players = room['players']
    _players = {}
    for player in players:
        _players[player] = choice(app.ctx.powers.all())['id']
    app.ctx.rooms.update({'players': _players}, where("name") == room_name)
    for user_id in app.ctx.connections[room_name]:
        await app.ctx.connections[room_name][user_id].put('new power')
    return DatastarResponse()
