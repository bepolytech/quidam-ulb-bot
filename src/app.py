# cas-sso-discord-bot

import os
from os import getenv, path
from dotenv import load_dotenv
load_dotenv()

from typing import Optional, List
import sys
import signal

from cas import CASClient # https://github.com/Chise1/fastapi-cas-example # python_cas ?
from fastapi import FastAPI, Depends, Request, status, Path
import uvicorn
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_discord import DiscordOAuthClient, RateLimited, Unauthorized # https://github.com/Tert0/fastapi-discord
#* OR ? :
#* from starlette_discord.client import DiscordOAuthClient # https://github.com/nwunderly/starlette-discord
from fastapi_discord import User as DiscordUser
#from fastapi_discord import Role as DiscordRole #?
from fastapi_discord import Guild as DiscordGuild
from fastapi_discord.exceptions import ClientSessionNotInitialized
from fastapi_discord.models import GuildPreview
import asyncio
import anyio
import logging
import platform
from typing import Annotated #TODO: use Annotated
from time import time
from httpx import AsyncClient
from contextlib import asynccontextmanager
from json import load

from utils import addLoggingLevel
#from bot import Bot # TODO: implement bot
from locales import Locale, DEFAULT_LANG

# ------------


VERSION = "2.0.0-alpha6"


# ------------

DEBUG=True if getenv("DEBUG") or getenv("DEBUG") is not None or getenv("DEBUG") != "" else False

logger = logging.getLogger("app")

templates = Jinja2Templates(directory="src/templates")

#locale: Locale = Locale(debug=DEBUG)

def init():
    #logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    #stream_handler = logging.StreamHandler(sys.stdout)
    #log_formatter = logging.Formatter("{name}:[{levelname}] {asctime} [{processName}-{threadName}] ({filename}:{lineno}) {message}" if DEBUG else "{name}:[{levelname}] {asctime} ({filename}:{lineno}) {message}", style="{", datefmt="%Y-%m-%d %H:%M")
    #stream_handler.setFormatter(log_formatter)
    #logger.addHandler(stream_handler)

    if DEBUG:
        #logging.basicConfig(level=logging.DEBUG)
        logging.basicConfig(
            level=logging.DEBUG,
            format="{name}:[{levelname}] {asctime} [{processName}-{threadName}] ({filename}:{lineno}) {message}",
            style="{",
            datefmt="%Y-%m-%d %H:%M"
        )
        logger.info("Debug mode enabled")
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="{name}:[{levelname}] {asctime} ({filename}:{lineno})  {message}", # [{processName}-{threadName}]
            style="{",
            datefmt="%Y-%m-%d %H:%M"
        )
    logger.info("Logger set.")

    logger.info("### Launching app...")
    logger.info("### CAS-SSO-Discord-Bot")
    logger.info("### Version: "+str(VERSION))
    logger.info("###------------------------")
    logger.info("### Name: "+str(getenv("APP_NAME")))
    logger.info("### Description: "+str(getenv("APP_DESCRIPTION")))
    logger.info("###------------------------")

    try:
        with open(path.join(path.dirname(__file__),"../config/cas_attributes_filter.json"), "r", encoding="utf-8") as cas_attr_file:
            app.cas_attr_filter = load(cas_attr_file)
        logger.info("Successfully loaded cas_attributes_filter.json:")
        logger.info(app.cas_attr_filter)
    except FileNotFoundError:
        logger.error("config/cas_attributes_filter.json not found, copy the cas_attributes_filter.json.example file, set your filtering CAS attributes and rename it to cas_attributes_filter.json")
        exit(1)

    #logger.info("Initializing DiscordClient")
    #discord_auth.init()
    # ------ init() end ------

# Discord OAuth Client
discord_auth = DiscordOAuthClient(
    client_id=getenv('DISCORD_CLIENT_ID'),
    client_secret=getenv('DISCORD_CLIENT_SECRET'),
    #redirect_url=getenv('DISCORD_REDIRECT_URI'),
    redirect_uri=(str(getenv("SITE_URL", "http://localhost:8000")) if not DEBUG else "http://localhost:8000")+"/discord-callback",
    scopes=("identify","guilds")#, "guilds", "email") # scopes default: just "identify"
)
logger.info(f"discord_auth scopes: {discord_auth.scopes.replace('%20', '_')}")
DISCORD_TOKEN_URL = "https://discord.com/api/v10/oauth2/token" # ? https://github.com/Tert0/fastapi-discord/issues/96
#DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token" # no version in url ?

class App(FastAPI):
    #locale: Locale
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locale: Locale = None # extends FastAPI with locale
        self.discord = discord_auth
        self.cas_attr_filter = None

@asynccontextmanager
async def lifespan(app: FastAPI): # replaces deprecated @app.on_event("startup") and @app.on_event("shutdown")
    # --- startup ---
    logger.info("FastAPI app startup")
    #TODO: create or init database here
    logger.info("Initializing DiscordClient")
    await app.discord.init()
    logger.info(f"discord_auth scopes: {app.discord.scopes.replace('%20', '_')}")
    app.locale = Locale(debug=DEBUG)
    templates.env.globals.update(lang_str=app.locale.lang_str) # get string from language file
    yield
    # --- shutdown ---
    logger.info("FastAPI app shutdown")

# FastAPI App
#app = FastAPI(
app = App(
    title=getenv("APP_NAME"),
    description=getenv("APP_DESCRIPTION"),
    version=VERSION,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")

# CAS Client
cas_client = CASClient(
    version=getenv('CAS_VERSION', 1),
    #service_url=getenv('CAS_SERVICE_URL', "http://localhost:8000/login"),
    service_url=(str(getenv('SITE_URL', "http://localhost:8000")) if not DEBUG else "http://localhost:8000")+"/login",
    server_url=getenv('CAS_SERVER_URL'),
    #validate_url=getenv('CAS_VALIDATE_URL', "/serviceValidate"),
)
CAS_VALIDATE_PATH = getenv('CAS_VALIDATE_PATH', "/serviceValidate") # or "/proxyValidate" ?

# Session Middleware for FastAPI
APP_SECRET_KEY = getenv('APP_SECRET_KEY')
if APP_SECRET_KEY is None:
    logger.error("APP_SECRET_KEY not set")
    exit(1)
app.add_middleware(SessionMiddleware, # https://www.starlette.io/middleware/#sessionmiddleware
                    secret_key=APP_SECRET_KEY,
                    max_age=int(getenv('SESSION_MAX_AGE',12*3600)), # 12*1 hour. Session expiry time in seconds. Defaults to 2 weeks. If set to None then the cookie will last as long as the browser session
                    same_site="strict", # flag prevents the browser from sending session cookie along with cross-site requests, default:"lax" or "strict"
                    https_only=False, # indicate that Secure flag should be set (can be used with HTTPS only), default:False
                    )


#admin_guild: DiscordGuild = DiscordGuild(
#    id=getenv("ADMIN_GUILD_ID"),
#    name=getenv("ADMIN_GUILD_NAME", "Admin Guild"),
#    owner=???,
#    permissions=???
#)

def env_var(key: str, default: Optional[str] = None):
    value = getenv(key, default)
    if value is None:
        logger.error(f"In Jinja2Template env_var(key) filter: Environment variable with key={key} is not set")
        return ""
    return value

def is_debug() -> bool:
    return DEBUG

# Provide Python functions inside Jinja templates :
templates.env.globals.update(env_var=env_var) # or templates.env.filter["env_var"] ?
#templates.env.globals.update(lang_str=app.locale.lang_str) # get string from language file
templates.env.globals.update(time=time) # get current time
templates.env.globals.update(is_debug=is_debug) # check if in debug mode


#@app.on_event("startup") #* DEPRECATED
#async def on_startup():
#    logger.info("Starting up...")
#    await discord_auth.init()
#    app.locale = Locale(debug=DEBUG)
#    templates.env.globals.update(lang_str=app.locale.lang_str) # get string from language file

@app.get('/teapot')
async def teapot():
    return HTMLResponse("<h1>This is a teapot 🫖</h1>", status_code=status.HTTP_418_IM_A_TEAPOT)
@app.get('/hello')
async def hello():
    return HTMLResponse("<h1>Hello, world!</h1>")

@app.get('/', response_class=RedirectResponse)
async def index_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower() # get first language from header
    if pref_lang in app.locale.lang_list:
        if DEBUG:
            logger.debug(f"index_without_lang: in Accept-Language header: {lang_header} => pref_lang={pref_lang}")
        return RedirectResponse(url=f"/{pref_lang}/")
    return RedirectResponse(url=f"/{DEFAULT_LANG}/", status_code=status.HTTP_308_PERMANENT_REDIRECT)

@app.get('/{lang}/', response_class=HTMLResponse)
async def index(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):# lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]
    if lang in ["favicon.ico"]:
        return
    request.session['lang'] = lang
    #check if user is already logged in in request.session, redirect to user if so
    user = request.session.get("user")
    if user:
        return RedirectResponse(url=f"/{lang}/user")
    
    return templates.TemplateResponse(name="index.jinja", context={"request": request,"hello": "world", "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('home_page_title', lang)})

@app.get('/profile')
async def profile_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower() # get first language from header
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/user")
    return RedirectResponse(url=f"/{DEFAULT_LANG}/user", status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/profile')
async def profile(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return RedirectResponse(url=f"/{lang}/user")
@app.get('/me')
async def me_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower() # get first language from header
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/user")
    return RedirectResponse(url=f"/{DEFAULT_LANG}/user", status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/me')
async def me(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return RedirectResponse(url=f"/{lang}/user")


@app.get('/user', response_class=RedirectResponse)
async def user_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower() # get first language from header
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/user")
    return RedirectResponse(url=f"/{DEFAULT_LANG}/user", status_code=status.HTTP_308_PERMANENT_REDIRECT)

@app.get('/{lang}/user', response_class=HTMLResponse)
async def user(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])], debug: Optional[str] = None, discorddebug: Optional[bool] = None): # lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]
    request.session['lang'] = lang
    if DEBUG:
        logger.debug(f"session.user: {request.session.get('user')}")
        if debug == APP_SECRET_KEY:
            logger.debug("Debug mode, user page accessed with ?debug=`app_key`")
            logger.debug(f"session: {request.session}")
            if discorddebug:
                logger.debug(f"Debug mode: user_with_discord page accessed with ?discorddebug=true")
                return templates.TemplateResponse(name="user_with_discord.jinja", context={"request": request,"cas_username": "debug_username", "cas_email": "debug_email@example.org", "discord_id": "000", "discord_username": "@debug_discord_username", "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('user_page_title', lang)})
            return templates.TemplateResponse(name="user.jinja", context={"request": request,"cas_username": "debug_username", "cas_email": "debug_email@example.org", "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('user_page_title', lang)})
    user = request.session.get("user")
    if DEBUG:
        logger.debug(f"user: {user}")
    # ---------------- user was CAS authenticated ----------------
    if user:
        # %%%%%%%%%%%%% user is Discord authenticated %%%%%%%%%%%%%%%%%
        if await discord_auth.isAuthenticated(request.session['access_token']):#TODO: or await db.user_linked_discord(cas_username=user):
            return templates.TemplateResponse(name="user_with_discord.jinja", context={"request": request,"cas_username": user, "cas_email": "test","discord_id": request.session['discord_id'], "discord_username": request.session['discord_username'], "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('user_page_title', lang)})
        # %%%%%%%%%%%%% user is not Discord authenticated %%%%%%%%%%%%%
        else:
            if DEBUG:
                cas_user = str(user['user'])
                logout_url = request.url_for('logout')
                return HTMLResponse(f'Logged in as {cas_user}. <a href="{logout_url}">Logout</a>')
            return templates.TemplateResponse(name="user.jinja", context={"request": request,"cas_username": user, "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('user_page_title', lang)})
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # ---------------- user is not CAS authenticated ----------------
    elif request.session.get("discord_token"):
        if DEBUG:
            logger.debug("user: discord_token exists but user is not CAS authenticated")
    if DEBUG:
        login_url = request.url_for('login')
        return HTMLResponse(f'Login required. <a href="{login_url}">Login</a>', status_code=status.HTTP_401_UNAUTHORIZED)
    #return RedirectResponse(request.url_for('login'), status_code=status.HTTP_401_UNAUTHORIZED)
    return RedirectResponse(request.url_for('index'), status_code=status.HTTP_401_UNAUTHORIZED)
    # ---------------------------------------------------------------


@app.get('/login')
async def login(request: Request, next: Optional[str] = None, ticket: Optional[str] = None):
    service_ticket = ticket # ST from user to verify with CAS server
    if request.session.get("user", None):
        # Already logged in
        return RedirectResponse(request.url_for('user', lang=request.session['lang']))

    # next = request.args.get('next')
    # ticket = request.args.get('ticket')
    # ---------------- log in to CAS (redirect to CAS server) --------------------
    if not service_ticket: # first login -> redirect to CAS
        # No ticket, the request come from end user, send to CAS login
        cas_login_url = cas_client.get_login_url()
        if DEBUG:
            logger.debug(f'CAS login URL: {cas_login_url}')
        return RedirectResponse(cas_login_url)
    # ------ log in to this service (callback from CAS server with ticket) -------

    # There is a ticket, the request come from CAS as callback.
    # need call `verify_ticket()` to validate ticket and get user profile.
    if DEBUG:
        logger.debug(f'service_ticket: {service_ticket}')
        logger.debug(f'next: {next}')

    # Send service ticket (ST) to CAS server to verify, get back user details as xml/dict
    user_from_cas, attributes_from_cas, pgtiou = cas_client.verify_ticket(service_ticket) # pgtIou means Proxy Granting Ticket IOU
    proxy_ticket = cas_client.get_proxy_ticket(pgtiou) # get Proxy Ticket (PT) for Proxy Callback
    user_from_cas_proxy, attributes_from_cas_proxy, pgtiou_proxy = cas_client.verify_ticket(proxy_ticket) # verify ticket again to get user details

    # Ony keep attributes that are in the filter (from file cas_attributes_filter.json)
    filtered_attributes = {key: value for key, value in attributes_from_cas.items() if key in app.cas_attr_filter}

    if DEBUG:
        logger.debug("Got response from ticket verification")
        logger.debug(f"proxy_ticket: {proxy_ticket}")
        logger.debug(f"user_from_cas_proxy, attributes_from_cas_proxy, pgtiou_proxy: {user_from_cas_proxy}, {attributes_from_cas_proxy}, {pgtiou_proxy}")
        logger.debug(f"CAS verify service_ticket response: user: {user_from_cas}, attributes: {attributes_from_cas}, pgtiou: {pgtiou}")
        logger.debug(f"attribute.cn (complete name) = {attributes_from_cas.get('cn')}, attribute.mail = {attributes_from_cas.get('mail')}, user = {user_from_cas}, attributes_from_cas.supannRefId = {attributes_from_cas.get('supannRefId')}, attributes_from_cas.supannRoleEntite (group) = {attributes_from_cas.get('supannRoleEntite')}")
        logger.debug(f"attributes_filter: {app.cas_attr_filter}")
        logger.debug(f"filtered_attributes: {filtered_attributes}")


    if not user_from_cas: # Failed to verify service_ticket
        login_url = request.url_for('login')
        if DEBUG:
            return HTMLResponse(f'Failed to verify ticket. <a href="{login_url}">Login</a>')
        return RedirectResponse(login_url)
    else:  # Login successfully, redirect according `next` query parameter.? or to /user
        #response = RedirectResponse(next)
        response = RedirectResponse(request.url_for('user', lang=request.session['lang']))
        request.session['user'] = dict(user=user_from_cas)
        return response


@app.get('/logout')
async def logout(request: Request):
    user = request.session.get("user")
    if user:
        redirect_url = request.url_for('logout_callback')
        cas_logout_url = cas_client.get_logout_url(redirect_url)
        if DEBUG:
            logger.debug('CAS logout URL: %s', cas_logout_url)
        return RedirectResponse(cas_logout_url)
    else:
        #login_url = request.url_for('login')
        #return HTMLResponse(f'Not logged in. <a href={login_url}>Login</a>')
        
        return RedirectResponse(request.url_for('login'))


@app.get('/logout-callback')
def logout_callback(request: Request):
    # redirect from CAS logout request after CAS logout successfully
    # response.delete_cookie('username')
    request.session.pop("user", None)
    
    #login_url = request.url_for('login')
    #return HTMLResponse(f'Logged out from CAS. <a href="{login_url}">Login</a>')
    
    return RedirectResponse(request.url_for('index'))


@app.get('/discord-login')
async def discord_login(request: Request):
    user = request.session.get("user")
    if DEBUG or user:
        # check if already logged in with discord, redirect to user if so
        if request.session.get("discord_token"):
            return RedirectResponse(request.url_for('user',lang=request.session['lang']))

        #TODO:
        #user_session_state = generate_random(seed=request.session.items())
        #session_state = randomASCII(len=12)
        #session_state = hashlib.sha256(os.urandom(1024)).hexdigest()

        #logging.debug("discord_login: "+discord_auth.get_oauth_login_url(state="my_test_state"))
        return RedirectResponse(discord_auth.get_oauth_login_url(
            state="my_test_state" #TODO: generate state token ? based on user's session/request (like: used as seed)? see above commented
        )) # TODO: state https://discord.com/developers/docs/topics/oauth2#state-and-security
        #return await discord_auth.login(request) # ?
        #return await RedirectResponse(discord_auth.oauth_login_url) # or discord_auth.get_oauth_login_url(state="my state")
    else:
        # user is not logged in with CAS
        return RedirectResponse(request.url_for('login'), status_code=status.HTTP_401_UNAUTHORIZED)


#TODO: NOT USE THIS ?
async def get_user(token: str = Depends(discord_auth.get_token)):
    if "identify" not in discord_auth.scopes:
        raise discord_auth.ScopeMissing("identify")
    route = "/users/@me"
    return DiscordUser(**(await discord_auth.request(route, token)))
    #return user
#TODO: NOT USE THIS ?
async def get_user_guilds(token: str = Depends(discord_auth.get_token)):
    if "guilds" not in discord_auth.scopes:
        raise discord_auth.ScopeMissing("guilds")
    route = "/users/@me/guilds"
    return [DiscordGuild(**guild) for guild in await discord_auth.request(route, token)]
    #return guilds


@app.get('/discord-callback')
async def discord_callback(request: Request, code: str, state: str):
    cas_user = request.session.get("user")
    if DEBUG or cas_user:
        logger.debug(f"discord_callback: code={code}, state={state}")

        token, refresh_token = await discord_auth.get_access_token(code) # ?
        if getenv("DEBUG"):
            logger.debug(f"discord_callback: token={token}, refresh_token={refresh_token}")
        request.session['discord_refresh_token'] = refresh_token
        request.session['discord_token'] = token #await discord_auth.get_token(request=request) #! or just token from above ?

        #TODO: get user info from discord
        user: DiscordUser = await get_user(token=token)

        if DEBUG:
            logger.debug(f"discord_callback: get_user.username: {user.username}")
        ###user: DiscordUser = await discord_auth.user(request=request)
        request.session['discord_username'] = user.username+(str(user.discriminator) if user.discriminator else "") # ?
        request.session["discord_global_name"] = user.global_name
        request.session["discord_id"] = user.id
        # avatar is https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png

        try:
            logger.debug("discord_callback: getting user guilds")
            #TODO: get user guilds from discord
            all_user_guilds: List[DiscordGuild] = await get_user_guilds(token=token)
            if getenv("DEBUG"):
                logger.debug(f"discord_callback: user_guilds[0]={all_user_guilds[0]}")
            ###user_guilds: List[DiscordGuild] = await discord_auth.guilds()

            #user_guilds = [x for x in all_user_guilds if x in db.get_all_guilds()] #TODO: check which guilds that the user and the bot are both in, and only put the intersect in the session
            #request.session["discord_guilds"] = user_guilds
        except:
            logger.error(f"ScopeMissing error in Discord API Client: missing \"guilds\" in scopes -> ignoring user guilds")

        try:
            assert state == "my_test_state" # compares state for security # TODO: assert state
        except AssertionError:
            request.session.clear()
            logger.error("discord_callback: state does not match")
            return RedirectResponse(request.url_for('login'), status_code=status.HTTP_406_NOT_ACCEPTABLE)

        return RedirectResponse(request.url_for('user', lang=request.session['lang']))
        ##try:
        ##    await discord_auth.callback(request)
        ##    return RedirectResponse(request.url_for('user'))
        ##except Unauthorized:
        ##    return JSONResponse({"error": "Unauthorized"}, status_code=401)
        ##except RateLimited:
        ##    return JSONResponse({"error": "RateLimited"}, status_code=429)
        ##except ClientSessionNotInitialized:
        ##    return JSONResponse({"error": "ClientSessionNotInitialized"}, status_code=500)
    else:
        return RedirectResponse(request.url_for('login'), status_code=status.HTTP_401_UNAUTHORIZED)


#! needed ?
@app.get(
    "/discord-authenticated",
    response_model=bool,
)
async def isDiscordAuthenticated(request: Request):
    try:
        auth = await discord_auth.isAuthenticated(token=request.session['discord_token'])
        return auth
    except Unauthorized:
        return False


@app.get('/discord-logout')#, dependencies=[Depends(discord_auth.requires_authorization)])
async def discord_logout(request: Request):#, token: str = Depends(discord_auth.get_token)):
    try:
        #if await discord_auth.isAuthenticated(token):
        if await discord_auth.isAuthenticated(request.session['discord_token']):
            if DEBUG:
                logger.debug("discord_logout: isAuthenticated=True")
    #if await discord_auth.isAuthenticated(request.session['access_token']):
            
            # TODO: sufficient ?

            #await discord_auth.revoke(request.session['discord_token']) #? not in fastapi-discord ?
            # see https://github.com/treeben77/discord-oauth2.py/blob/main/discordoauth2/__init__.py#L242
            if DEBUG:
                logger.debug("discord_logout: revoking discord_token")
            await revoke_discord_token(request.session['discord_token'], "access_token", request.session['discord_username'])
            if DEBUG:
                logger.debug("discord_logout: revoking discord_refresh_token")
            await revoke_discord_token(request.session['discord_refresh_token'], "refresh_token", request.session['discord_username'])

            request.session.pop("discord_token", None)
            request.session.pop("discord_refresh_token", None)
            request.session.pop('discord_username', None)
            request.session.pop("discord_global_name", None)
            request.session.pop("discord_id", None)
            request.session.pop("discord_guilds", None)
        
        return RedirectResponse(request.url_for('user', lang=request.session['lang']))
    
    except Unauthorized:
        cas_user = request.session.get("user")
        if cas_user:
            return RedirectResponse(request.url_for('user', lang=request.session['lang']))
        else:
            return RedirectResponse(request.url_for('login'), status_code=status.HTTP_401_UNAUTHORIZED)
    except KeyError:
        if request.session['lang'] in app.locale.lang_list:
            return RedirectResponse(url=f"/{request.session['lang']}/user")
        else:
            return RedirectResponse(url=f"/{DEFAULT_LANG}/user")

# FIXME: #! doesn't seem to be working ?
async def revoke_discord_token(token: str, token_type: str=None, user: str=None):
    """
    Custom discord user token revoke implementation (which is missing from fastapi-discord).
    """
    async with AsyncClient(app=app, base_url=DISCORD_TOKEN_URL) as ac:
        response = await ac.post(
            "/revoke",
            data={"token": token, "token_type_hint": token_type},
            auth=(discord_auth.client_id, discord_auth.client_secret)
        )
        
    if response.status_code == 200:# or response.status_code.OK ?:
        if DEBUG:
            logger.debug(f"revoke_discord_token: Discord token (type:{token_type}) revoked successfully for user:{user}.")
        return True
    elif response.status_code == 401:
        logger.error(f"revoke_discord_token: 401 This AccessToken does not have the necessary scope.")
    elif response.status_code == 429:
        logger.error(f"revoke_discord_token: 429 You are being Rate Limited. Retry after: {response.json()['retry_after']}")
    else:
        logger.error(f"revoke_discord_token: Unexpected HTTP response {response.status_code}")
    return False

#TODO: rate limit
@app.post('/user/force-add-roles') # POST request
async def force_add_roles(request: Request):
    if DEBUG:
        logger.debug("force_add_roles: force adding roles to user in all user's guilds")
    user_cas_username = request.session.get("cas_username")
    user_discord_id = request.session.get("discord_id")
    # TODO: add roles to user in all guilds
    # get all guilds the user is in with ormar
    """
    user_guilds = await User.objects.select_related('guilds').get(discord_id=user_discord_id) # or User.objects.select_related('guilds').get(cas_username=user_cas_username)
    for guild in user_guilds:
        await bot.add_roles(guild.discord_guild_id, user_discord_id)
    """
    if request.session['lang'] in app.locale.lang_list:
        #return RedirectResponse(url=f"/{request.session['lang']}/user", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(request.url_for('user', lang=request.session['lang']), status_code=status.HTTP_303_SEE_OTHER)
    else:
        #return RedirectResponse(url=f"/{DEFAULT_LANG}/user", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(request.url_for('user', lang=DEFAULT_LANG), status_code=status.HTTP_303_SEE_OTHER)

# ---- other pages ----

@app.get('/help')
async def help_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower()
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/help")
        return RedirectResponse(url_for('help', lang=pref_lang))
    return RedirectResponse(url=f"/{DEFAULT_LANG}/help", status_code=status.HTTP_308_PERMANENT_REDIRECT)
    return RedirectResponse(url_for('help', lang=DEFAULT_LANG), status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/help', response_class=HTMLResponse)
async def help(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return templates.TemplateResponse(name="help.jinja", context={"request": request, "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('help_page_title', lang)})

@app.get('/about')
async def about_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower()
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/about")
        return RedirectResponse(url_for('about', lang=pref_lang))
    return RedirectResponse(url=f"/{DEFAULT_LANG}/about", status_code=status.HTTP_308_PERMANENT_REDIRECT)
    return RedirectResponse(url_for('about', lang=DEFAULT_LANG), status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/about', response_class=HTMLResponse)
async def about(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return templates.TemplateResponse(name="about.jinja", context={"request": request, "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('about_page_title', lang)})

@app.get('/privacy-policy')
async def privacy_policy_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower()
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/privacy-policy")
        return RedirectResponse(url_for('privacy_policy', lang=pref_lang))
    return RedirectResponse(url=f"/{DEFAULT_LANG}/privacy-policy", status_code=status.HTTP_308_PERMANENT_REDIRECT)
    return RedirectResponse(url_for('privacy_policy', lang=DEFAULT_LANG), status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/privacy-policy', response_class=HTMLResponse)
async def privacy_policy(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return templates.TemplateResponse(name="privacypolicy.jinja", context={"request": request, "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('privacy_policy', lang)})

@app.get('/terms-of-service')
async def terms_of_service_without_lang(request: Request):
    lang_header = request.headers["Accept-Language"]
    pref_lang = lang_header.split(',')[0].split(';')[0].strip().split('-')[0].lower()
    if pref_lang in app.locale.lang_list:
        return RedirectResponse(url=f"/{pref_lang}/terms-of-service")
        return RedirectResponse(url_for('terms_of_service', lang=pref_lang))
    return RedirectResponse(url=f"/{DEFAULT_LANG}/terms-of-service", status_code=status.HTTP_308_PERMANENT_REDIRECT)
    return RedirectResponse(url_for('terms_of_service', lang=DEFAULT_LANG), status_code=status.HTTP_308_PERMANENT_REDIRECT)
@app.get('/{lang}/terms-of-service', response_class=HTMLResponse)
async def terms_of_service(request: Request, lang: Annotated[str, Path(title="2-letter language code", max_length=2, min_length=2, examples=["en","fr"])]):
    return templates.TemplateResponse(name="tos.jinja", context={"request": request, "current_lang": lang, "lang_list": app.locale.lang_list, "page_title": app.locale.lang_str('terms_of_service', lang)})

# ---- error pages ----

@app.exception_handler(404)
async def not_found_error_handler(request: Request, exc: Exception):
    return HTMLResponse(templates.TemplateResponse(name="404.html", context={"request": request, "current_lang": DEFAULT_LANG}, status_code=404))
    #return JSONResponse({"error": "Not Found"}, status_code=404)

#@app.exception_handler(500)
#async def internal_error_handler(request: Request, exc: Exception):
#    return HTMLResponse(templates.TemplateResponse(name="500.html", context={"request": request, "current_lang": DEFAULT_LANG}, status_code=500))

@app.exception_handler(403)
async def forbidden_error_handler(request: Request, exc: Exception):
    return HTMLResponse(templates.TemplateResponse(name="403.html", context={"request": request, "current_lang": DEFAULT_LANG}, status_code=403))

@app.exception_handler(Unauthorized)
async def unauthorized_error_handler(request: Request):
    error = "Unauthorized"
    return HTMLResponse(templates.TemplateResponse(name="401.html", context={"request": request,"error": error, "current°lang": DEFAULT_LANG}, status_code=401))


@app.exception_handler(RateLimited)
async def rate_limit_error_handler(request: Request, e: RateLimited):
    return HTMLResponse(templates.TemplateResponse(name="429.html", context={"request": request,"retry_after": e.retry_after, "current°lang": DEFAULT_LANG}, status_code=429))


#@app.exception_handler(ClientSessionNotInitialized)
#async def client_session_error_handler(request: Request, e: ClientSessionNotInitialized):
#    logger.error(e)
#    return HTMLResponse(templates.TemplateResponse(name="500.html", context={"request": request,"error": e, "current_lang": DEFAULT_LANG}, status_code=500))

##@app.exception_handler(Exception)
##async def generic_error_handler(request: Request, e: Exception):
##    logger.error(e)
##    return HTMLResponse(templates.TemplateResponse(name="error.html", context={"request": request,"error": e, "current_lang": DEFAULT_LANG}, status_code=500))


# ----------------------------------------------------------------------------



async def run_bot():

    addLoggingLevel("TRACE", logging.INFO - 5)
    botLogFormatter = logging.Formatter("{name}:[{levelname}] {asctime} [{processName}-{threadName}] ({filename}:{lineno}) {message}" if DEBUG else "{name}:[{levelname}] {asctime} ({filename}:{lineno}) {message}", style="{", datefmt="%Y-%m-%d %H:%M")
    botRootLogger = logging.getLogger("bot")
    botRootLogger.setLevel(logging.DEBUG) # if DEBUG else logging.TRACE ?

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(botLogFormatter)
    consoleHandler.setLevel(logging.TRACE)
    botRootLogger.addHandler(consoleHandler)

    if platform.system() == "Linux":
        fileInfoHandler = logging.handlers.RotatingFileHandler(
            filename="logs/info.log", mode="w", encoding="UTF-8", delay=True, backupCount=5
        )
        fileDebugHandler = logging.handlers.RotatingFileHandler(
            filename="logs/debug.log", mode="w", encoding="UTF-8", delay=True, backupCount=5
        )
        fileInfoHandler.setFormatter(botLogFormatter)
        fileInfoHandler.setLevel(logging.TRACE)
        fileInfoHandler.doRollover()
        botRootLogger.addHandler(fileInfoHandler)
        fileDebugHandler.setFormatter(botLogFormatter)
        fileDebugHandler.setLevel(logging.DEBUG)
        fileDebugHandler.doRollover()
        botRootLogger.addHandler(fileDebugHandler)

    else:
        logging.warning("Non-Linux system. INFO and DEBUG log files won't be available.")

    #bot = Bot(logger=botRootLogger, logFormatter=botLogFormatter)

    # TODO: implement bot
    BOT_TOKEN = getenv("DISCORD_BOT_TOKEN")
    #if not BOT_TOKEN:
    #    raise ValueError("No bot token provided. Set the DISCORD_BOT_TOKEN environment variable.")
    #await bot.run(BOT_TOKEN)



async def run_webapp():
    host = str(getenv('FASTAPI_HOST', 'localhost'))
    port = int(getenv('FASTAPI_PORT', 8000))
    reload = True if getenv('DEV_ENV', False) is not None and getenv('DEV_ENV', False) != "" else False
    debug = True if getenv('DEBUG', False) is not None and getenv('DEBUG', False) != "" else False

    logger.debug(f"Running FastAPI webapp{ "%%%%% WITH RELOAD %%%%%" if reload else "" }")
    logger.debug(f"with port={port}, host={host}")

    config = uvicorn.Config(app, host=host, port=port, log_level="debug" if debug else "info", reload=reload)
    server = uvicorn.Server(config)

    await server.serve()


# ----------------------------------------------------------------------------

async def shutdown_signal_listener(task_group):
    """
    Listens for shutdown signals and cancels the task group when received.
    This function only adds signal handlers on Unix-like systems.
    On Windows, it relies on KeyboardInterrupt.
    """
    if os.name != 'nt':
        # Only Unix-like systems support signal handling with AnyIO
        with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signal_aiter:
            async for sig in signal_aiter:
                print(f"Received signal: {sig}. Initiating shutdown.")
                task_group.cancel_scope.cancel()
    else:
        # On Windows, rely on KeyboardInterrupt (Ctrl+C)
        try:
            await anyio.sleep_forever()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received. Initiating shutdown.")
            task_group.cancel_scope.cancel()

async def main():
    async with anyio.create_task_group() as tg:
        tg.start_soon(run_webapp)
        #TODO: start Discord bot
        #tg.start_soon(run_bot)

        # Necessary for graceful shutdowns ? seems to double launch the webapp:
        #tg.start_soon(shutdown_signal_listener, tg)

"""
async def run_tasks():
    host = str(getenv('FASTAPI_HOST', 'localhost'))
    port = int(getenv('FASTAPI_PORT', 8000))
    reload = True if getenv('DEV_ENV', False) is not None and getenv('DEV_ENV', False) != "" else False
    logger.debug(f"Running FastAPI webapp{ "%%%%% WITH RELOAD %%%%%" if reload else "" }")
    logger.debug(f"with port={port}, host={host}")

    web_task = asyncio.create_task(uvicorn.run("app:app", port=port, host=host, reload=reload))

    
    bot_task = asyncio.create_task(run_bot())

    await asyncio.gather(web_task, bot_task)
"""

if __name__ == '__main__':
    init()

    try:
        anyio.run(main) # run FastAPI webapp and Disnake bot as Anyio async tasks
        #asyncio.run(run_tasks()) # run FastAPI webapp and Disnake bot as async tasks
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt: Shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.info("Shutting down...")

#EOF