"""Rebrand a separately mounted copy of the verified custom test image."""
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
ROOT=Path('/var/tmp/ecofi-brand-v1/root')
APP=ROOT/'.cache/tmp/55/05/pfi'
VIEWS=APP/'resources/views'
PUBLICS=[APP/'public',ROOT/'var/www/html/pisofi/public']
LOGS=HERE/'logs/branding'
LOGS.mkdir(parents=True,exist_ok=True)
subprocess.run(['mountpoint','-q',str(ROOT)],check=True)
changes=[]
def put(p,data):
    assert ROOT in p.parents
    for parent in [p]+list(p.parents):
        if parent==ROOT:break
        assert not parent.is_symlink(),str(parent)
    raw=data.encode() if isinstance(data,str) else data
    before=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    if before==hashlib.sha256(raw).hexdigest():return
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(raw)
    if before is None:p.chmod(0o644)
    changes.append({'path':'/'+str(p.relative_to(ROOT)),'before':before,'after':hashlib.sha256(raw).hexdigest()})

brand_names=re.compile(r'\b(?:PisoFi|PISOFI|Piso-Fi|Piso Fi)\b')
# Keep third-party notices verbatim in a local provenance archive.
for p in VIEWS.rglob('*.twig'):
    s=p.read_text()
    if 'copyright' in s.lower() or 'hold PisoFi harmless' in s:
        put(ROOT/'usr/share/doc/ecofi/upstream-templates'/p.relative_to(VIEWS),s)
    # The case-sensitive brand spellings are distinct from the Pisofi JS namespace.
    s=brand_names.sub('Eco-Fi',s)
    s=s.replace('img/logo.svg','ecofi/logo.jpg')
    s=re.sub(r'https?://(?:www\.|client\.|dash\.)?pisofiph\.com[^\s\"\'<>]*','/ecofi/about.html',s)
    # Each complete layout loads the overrides after its own inline styles.
    if '</head>' in s:
        s=s.replace('</head>','<link rel="stylesheet" href="/ecofi/ecofi-brand.css?v=1">\n</head>')
    if p.name in ['app.twig','apphome.twig','app.desktop.home.twig']:
        if p.name=='app.twig':
            s=s.replace('hold-transition','ecofi-admin hold-transition')
        else:s=s.replace('<body>','<body class="ecofi-portal">')
    put(p,s)

p=VIEWS/'templates/partials/leftnav.twig'
s=p.read_text().replace('{{ site_info.site_name}}</span>','Eco-Fi MASTER</span>')
put(p,s)
p=VIEWS/'auth/signin.twig'
s=p.read_text().replace('<b>{{ site_info.site_name}}</b>','<b>Eco-Fi MASTER</b>')
s=s.replace('Sign in to start your session','Sign in to manage your Eco-Fi')
put(p,s)
# Repair the inherited malformed hidden footer while replacing its branding.
p=VIEWS/'templates/partials/footer.twig'
s=p.read_text();end=s.index('<!-- Control Sidebar -->')
put(p,'{% spaceless %}\n{% if not hidesignin %}<footer class="main-footer text-center">Eco-Fi Master &middot; <a href="/ecofi/about.html">About this system</a></footer>{% endif %}\n\n'+s[end:])
# Existing banner carousel remains functional; this is the current Eco-Fi hero.
for name in ['portal.home.twig','portal.desktop.twig']:
    p=VIEWS/name;s=p.read_text()
    marker='{% block contents %}'
    if marker in s:s=s.replace(marker,marker+'\n<div class="container pt-3"><img class="ecofi-banner" src="/ecofi/banner-main.jpg" alt="SMART Eco-Fi Vendo System — Turning Plastic into Connectivity"></div>',1)
    put(p,s)

about='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>About Eco-Fi</title><link rel="icon" href="/ecofi/favicon.ico"><link rel="stylesheet" href="/ecofi/ecofi-brand.css"></head><body><main class="ecofi-about"><img class="ecofi-banner" src="/ecofi/banner-main.jpg" alt="SMART Eco-Fi Vendo System"><h1>Eco-Fi</h1><p>Turning Plastic into Connectivity.</p><p><a href="/">Open portal</a> &nbsp; <a href="/admin">Administrator sign in</a></p><h2>System information</h2><p>Eco-Fi branding edition 1, based on the custom PisoFi 5.3.0 test image. Uses the existing local signed licensing system.</p><p>The underlying Armbian, Debian, PisoFi application and bundled third-party components retain their respective authorship and notices. This edition does not claim ownership of those components. Their installed notices remain under /usr/share/doc and application vendor directories; original branded template notices are archived under /usr/share/doc/ecofi/upstream-templates.</p><p>Rebranding does not add bottle-credit integration with the separate Eco-Fi Python application.</p></main></body></html>'''
assets=['logo.jpg','admin-logo.jpg','banner-main.jpg','favicon.ico','favicon-16x16.png','favicon-32x32.png','apple-touch-icon.png']
for public in PUBLICS:
    for name in assets:put(public/'ecofi'/name,(PROJECT/'host/static'/name).read_bytes())
    put(public/'ecofi/ecofi-brand.css',(HERE/'payload/ecofi-brand.css').read_bytes())
    put(public/'ecofi/about.html',about)
    put(public/'favicon.ico',(PROJECT/'host/static/favicon.ico').read_bytes())
    for p in public.rglob('*.html'):
        if 'plugins' in p.parts or 'ecofi' in p.parts:continue
        s=p.read_text(errors='replace')
        if brand_names.search(s):put(p,brand_names.sub('Eco-Fi',s))

# Also change fallback site branding in PHP string literals, including its
# obfuscated byte-escape spelling. Class names, paths and database keys stay intact.
p=APP/'app/Pisofi/PortalManager.php'
s=p.read_text()
for before,after in [('PisoFi','Eco-Fi'),('Discover the power of Piso','Turning Plastic into Connectivity'),('img/logo.svg','ecofi/logo.jpg')]:
    escaped=''.join('\\x%02x'%ord(c) for c in before)
    octal=''.join('\\%03o'%ord(c) for c in before)
    s=s.replace("'"+before+"'","'"+after+"'").replace('"'+before+'"','"'+after+'"').replace(escaped,after).replace(octal,after)
put(p,s)
put(ROOT/'etc/hostname','ecofi-vendo\n')
hosts=(ROOT/'etc/hosts').read_text()
hosts=re.sub(r'^127\.0\.1\.1.*$','127.0.1.1 ecofi-vendo',hosts,flags=re.M)
put(ROOT/'etc/hosts',hosts)
put(ROOT/'etc/ecofi/brand.json',json.dumps({'name':'Eco-Fi','edition':'Branding 1','base':'PisoFi_Custom_Test_v1','tagline':'Turning Plastic into Connectivity'},indent=2)+'\n')
put(ROOT/'tmp/ecofi-brand-database.php',(HERE/'payload/brand_database.php').read_bytes())
# Cached Twig output must be rebuilt from the new templates on the device.
cache=APP/'cache';cleared=0
for p in cache.rglob('*'):
    if p.is_file() and not p.is_symlink():
        p.unlink();cleared+=1
(LOGS/'changes.json').write_text(json.dumps({'changes':changes,'compiled_templates_cleared':cleared},indent=2))
print(json.dumps({'changed_files':len(changes),'compiled_templates_cleared':cleared}),flush=True)
