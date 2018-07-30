# -*- coding: utf-8 -*-
import os
import math
import random
import io
import urllib
import re

import flask
import mwoauth
import mwclient
import yaml
import sqlalchemy
import pendulum


app = flask.Flask(__name__)


DESCRIPTION_TEMPLATE = """== {{{{int:filedesc}}}} ==
{{{{Photograph
| Description    = 500px provided description: {description} [{tags}]
| Date           = {iso_date}
| Source         = {source}
| photographer   = {author}
| Permission     = {permission}
| title          = {title}
}}}}
{location}
{{{{imported with import-500px}}}}"""
LICENSE_MAP = {
    4: '{{Cc-by-3.0}}',
    6: '{{Cc-by-sa-3.0}}',
    7: ('PDM (Public Domain Mark) (as stated in 500px, please identify the '
        'reason for a valid license and change the license template'),
    8: '{{Cc-zero}}'}

# Load configuration from YAML file
__dir__ = os.path.dirname(__file__)
app.config.update(
    yaml.safe_load(open(os.path.join(__dir__, 'config.yaml'))))

con = sqlalchemy.create_engine(app.config['DBCONSTR'],  pool_recycle=600)


@app.route('/', methods=['GET'])
def index():
    username = flask.session.get('username', None)
    perpage = int(flask.request.args.get('perpage', '20'))
    total_pages = math.ceil(211335/perpage)
    page = int(flask.request.args.get('page', random.randint(1, total_pages)))
    return flask.render_template(
        'index.html', page=page, page_name='page', total_pages=total_pages,
        username=username, perpage=perpage)


@app.route('/pdm', methods=['GET'])
def pdm():
    username = flask.session.get('username', None)
    page = int(flask.request.args.get('page', '1'))
    perpage = int(flask.request.args.get('perpage', '20'))
    total_pages = math.ceil(20013/perpage)
    return flask.render_template(
        'index.html', page=page, page_name='pdm/page', total_pages=total_pages,
        username=username, perpage=perpage)


@app.route('/author/<int:author_id>', methods=['GET'])
def author(author_id):
    username = flask.session.get('username', None)
    page = int(flask.request.args.get('page', '1'))
    perpage = int(flask.request.args.get('perpage', '20'))
    r = con.execute(('select count(*) from s53823__importpx500.photos '
                    'where author_id=%s'), author_id)
    total_pages = math.ceil(r.fetchone()[0]/perpage)
    return flask.render_template(
        'index.html', page=page, total_pages=total_pages, username=username,
        page_name='author/{}/page'.format(author_id), perpage=perpage)


def high_quality_url(photo):
    """Extract the highest quality url from the photo json"""

    return [url for url in photo['image_url'] if 'm%3D2048/' in url][0]


def author_info(user_id):
    with open('/data/project/import-500px/users/{}.json'.format(user_id)) as j:
        author = flask.json.loads(j.read())
    return {k: v for k, v in author.items() if k in ["id", "about", "domain",
            "fullname", "username"]}


def author_from_photo(photo):
    author = photo['url'].split('by-')[-1].replace('-', ' ')
    author = re.sub(r'\b\w', lambda x: x.group().upper(), author)
    author = urllib.parse.unquote(author)
    return author


def build_description(photo):
    author = author_from_photo(photo)
    location = ''
    description = photo['description'] or photo['name']
    if photo['latitude'] and photo['longitude']:
        location = '{{{{Location |{} |{}}}}}'.format(
            photo['latitude'], photo['longitude'])
    return DESCRIPTION_TEMPLATE.format(
        description=description,
        tags=' ,'.join(['#' + t for t in photo['tags']]),
        iso_date=pendulum.parse(photo['taken_at']).in_tz('UTC').format(
            'YYYY-MM-DD HH:mm:ss (zz)'),
        source=('Imported from [https://500px.com{} 500px]. Archived at'
                ' [https://web.archive.org/web/201807id_/{} the Web Archive]'
                ' by [https://www.archiveteam.org/index.php?title=500px the'
                ' Archive Team].').format(photo['url'],
                                          high_quality_url(photo)),
        author='[https://500px.com/{} {}]'.format(
            author_info(photo['user_id'])['username'], author),
        permission=LICENSE_MAP[photo['license_type']],
        location=location, title=photo['name'])


@app.route('/upload/<int:photo_id>', methods=['POST'])
def upload(photo_id):
    username = flask.session.get('username', None)
    if username:
        site = mwclient.Site(
            'commons.wikimedia.org',
            consumer_token=app.config['CONSUMER_KEY'],
            consumer_secret=app.config['CONSUMER_SECRET'],
            access_token=flask.session['access_token']['key'],
            access_secret=flask.session['access_token']['secret'])
    else:
        flask.abort(403)
    with open('/data/project/import-500px/metadata/{}.json'.format(photo_id)) as j:
        photo = flask.json.loads(j.read())

    url = high_quality_url(photo)
    photo['file'] = io.BytesIO(urllib.request.urlopen(url).read())
    try:
        result = site.upload(
            file=photo['file'],
            filename='{} ({}).jpeg'.format(photo['name'], photo['id']),
            description=build_description(photo),
            comment=("Photo {} imported from 500px"
                     " with [[:wikitech:Tool:import-500px|"
                     "import-500px]]").format(photo['name']))
    except Exception as e:
        print(e)
        result = e
    return flask.json.dumps(result)


@app.route('/photo/<int:photo_id>', methods=['GET'])
def photo_detail(photo_id):
    with open('/data/project/import-500px/metadata/{}.json'.format(photo_id)) as j:
        photo = flask.json.loads(j.read())
    description = photo['description'] or photo['name']
    return flask.render_template(
        'item_detail.html', photo=photo, author=author_from_photo(photo),
        photo_str=flask.json.dumps(photo, indent=2), description=description,
        photo_url=high_quality_url(photo))


@app.route('/id/<int:photo_id>', methods=['GET'])
def photo_by_id(photo_id):
    r = con.execute(('select id, author, url, license '
                     'from s53823__importpx500.photos '
                     'where id = %s'), (photo_id, ))
    p = r.fetchall()
    return(flask.json.dumps([{'id': i[0], 'author': i[1], 'url': i[2],
                              'license': i[3]} for i in p]))


@app.route('/page/<int:page>', methods=['GET'])
def page(page, *args, **kwargs):
    return (_get_paginated_query(page, where='where license != 7',
                                 *args, **kwargs))


def _get_paginated_query(page, where='', *args, **kwargs):
    perpage = int(flask.request.args.get('perpage', '20'))
    if perpage > 300:
        flask.abort(422)
    offset = (page - 1) * perpage
    r = con.execute(('select id, author, url, license, low_res_url, author_id '
                     'from s53823__importpx500.photos {} '
                     'limit %s, %s').format(where), (offset, perpage))
    p = r.fetchall()
    return(flask.json.dumps([{'id': i[0], 'author': i[1], 'url': i[2],
                            'license': i[3], 'low_res_url': i[4],
                            'author_id': i[5]} for i in p]))


@app.route('/author/<int:author_id>/page/<int:page>', methods=['GET'])
def author_page(author_id, page, *args, **kwargs):
    return (_get_paginated_query(page, where='where author_id=%s' % author_id,
                                 *args, **kwargs))


@app.route('/pdm/page/<int:page>', methods=['GET'])
def pdm_page(page, *args, **kwargs):
    return (_get_paginated_query(page, where='where license = 7',
                                 *args, **kwargs))


@app.route('/login')
def login():
    """Initiate an OAuth login.

    Call the MediaWiki server to get request secrets and then redirect the
    user to the MediaWiki server to sign the request.
    """
    consumer_token = mwoauth.ConsumerToken(
        app.config['CONSUMER_KEY'], app.config['CONSUMER_SECRET'])
    try:
        redirect, request_token = mwoauth.initiate(
            app.config['OAUTH_MWURI'], consumer_token)
    except Exception:
        app.logger.exception('mwoauth.initiate failed')
        return flask.redirect(flask.url_for('index'))
    else:
        flask.session['request_token'] = dict(zip(
            request_token._fields, request_token))
        return flask.redirect(redirect)


@app.route('/oauth-callback')
def oauth_callback():
    """OAuth handshake callback."""
    if 'request_token' not in flask.session:
        flask.flash(u'OAuth callback failed. Are cookies disabled?')
        return flask.redirect(flask.url_for('index'))

    consumer_token = mwoauth.ConsumerToken(
        app.config['CONSUMER_KEY'], app.config['CONSUMER_SECRET'])

    try:
        access_token = mwoauth.complete(
            app.config['OAUTH_MWURI'],
            consumer_token,
            mwoauth.RequestToken(**flask.session['request_token']),
            flask.request.query_string)

        identity = mwoauth.identify(
            app.config['OAUTH_MWURI'], consumer_token, access_token)
    except Exception:
        app.logger.exception('OAuth authentication failed')

    else:
        flask.session['access_token'] = dict(zip(
            access_token._fields, access_token))
        flask.session['username'] = identity['username']

    return flask.redirect(flask.url_for('index'))


@app.route('/logout')
def logout():
    """Log the user out by clearing their session."""
    flask.session.clear()
    return flask.redirect(flask.url_for('index'))
