#!/usr/bin/python 

import ConfigParser
import BaseHTTPServer
import SimpleHTTPServer
import SocketServer
import sys
import re
import json
import subprocess
import threading
import Queue
import urlparse
import uuid
import os.path
import mimetypes
import shutil
import ssl
import ldap
import ldap.filter
import base64
import syslog

from pprint import pprint
from time import time, strftime, localtime

class ActiveDirectoryAuth:
  def __init__(self, uri, domain, debug=False, trace=False):
    self.uri = uri
    self.domain = domain
    print("Connecting to %s" % uri)
    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_ALLOW)
    trace_level = 0

    if debug:
      ldap.set_option(ldap.OPT_DEBUG_LEVEL, 4095)

    if trace:
      trace_level = 2

    self.ldap = ldap.initialize(self.uri, trace_level=trace_level)
    self.ldap.protocol_version = 3

  def debug(self):
    self.ldap = ldap.initialize(self.uri, trace_level=2)
    self.ldap.protocol_version = 3
    

  def authenticate(self, account_name, account_password):
    if account_password == "":
      self.last_error = { "user":account_name, "exception": "No password given" }
      return False
    try:
      uid = "%s\\%s" % ( self.domain, ldap.filter.escape_filter_chars(account_name) )
      self.last_result = { 
        "user": account_name,
        "result": self.ldap.simple_bind_s(uid, account_password)
      }
      if self.last_result:
        return True
      else:
        return False
    except ldap.LDAPError, e:
      self.last_error = { "user":account_name, "exception": e }
      return False

class SimpleAuth:
  def __init__(self, username, password):
    self.username = username
    self.password = password

  def authenticate(self,username,password):
    if self.username == username and self.password == password:
      return True
    else:
      return False

class ThreadingSimpleServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
  pass

class NSAutoHTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):

  path_regex = re.compile("^\/(?P<tool>\w+)(?:\/(?:(?P<action>[^\/]+)(?:\/(?P<arguments>.*)?)?)?)?$")
  tool_cache = {}
  default_action = {
    "short_text": "",
    "long_text": "",
    "fields": [],
    "args": "",
    "command": True,
    "require_auth": True,
    "subsequent_actions": [ "root" ]
  }
  default_template = """
    <html>
      <head>
        <title>{title}</title>
        <link rel="stylesheet" type="text/css" href="/static/css/main.css"/>
        <link rel="stylesheet" type="text/css" href="/static/css/{tool}.css"/>
      </head>
      <body>
        <div id="result"></div>
        <div id="form">{form}</div>
        <script type="text/javascript">
          var session_id = "{session_id}";
          var job_id = "{job_id}";
        </script>
        <script type="text/javascript" src="/static/js/events.js"></script>
        <script type="text/javascript" src="/static/js/{tool}.js"></script>
      </body>
    </html>
  """
  session_lock = threading.Lock()
  cache_lock = threading.Lock()
  jobs = {}
  sessions = {}
  concurrent_jobs = 10
  session_expiry = 3600

  @classmethod
  def set_tools(self,tools):
    self.tools = tools

  def get_tool(self,tool):
    try:
      self.cache_lock.acquire()
      if tool not in self.tool_cache or time() >= self.tool_cache[tool]["expiry"]:
        if tool in os.listdir(os.curdir + "/tools/"):
          executable_name = os.curdir + "/tools/" + tool
        else:
          raise Exception("Tool not found: %s" % tool)

        # re-cache the tool data
        process = subprocess.Popen([ executable_name, "--dump-ui-options"], stdout = subprocess.PIPE)
        data = json.load( process.stdout )
        self.tool_cache[tool] = {
          'cache': data,
          'expiry': time() + 600,
        }
        syslog.syslog("loaded ui options for %s" % tool)
        print "Updated the tool cache for %s" % tool
    finally: 
      self.cache_lock.release()
    return self.tool_cache[tool]['cache']

  def get_session(self,session_id = None):
    try:
      self.session_lock.acquire()
      now = time()
      for session in self.sessions:
        expiry = self.get_session_var(session,'expires')
        if now > expiry:
          self.sessions.pop(session)

      if session_id is None or session_id not in self.sessions:
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
          'lock': threading.Lock(),
        }

      self.sessions[session_id]['expires'] = now + self.session_expiry
    finally:
      self.session_lock.release()
    return session_id

  def get_session_var(self,session,key):
    session_dict = self.sessions[session]
    try:
      if type(key) != list:
        key = [key]
      session_dict['lock'].acquire()
      cursor = session_dict
      final_key = key.pop()
      for k in key:
        cursor = cursor[k]
      value = cursor[final_key]
    finally:
      session_dict['lock'].release()
    return value

  def set_session_var(self,session,key,value):
    session_dict = self.sessions[session]
    try:
      if type(key) != list:
        key = [key]
      session_dict['lock'].acquire()
      cursor = session_dict
      final_key = key.pop()
      for k in key:
        if k not in cursor:
          cursor[k] = {}
        cursor = cursor[k]
      cursor[final_key] = value
    finally:
      session_dict['lock'].release()
    return value

  def authorize(self, session_id):
    auth_token = self.headers.getheader('Authorization')

    if auth_token is None:
      return False

    if auth_token.startswith("Basic "):
      mode, base64_key = auth_token.split(" ")
      key = base64.b64decode(base64_key)
      username, _, password = key.partition(":")
      if self.auth_module.authenticate(username, password):
        self.set_session_var(session_id, [ 'auth', 'user' ], username)
        return True
      else:
        return False

    return False

      
  def display_action(self,tool,action_name,params={}):
    cache = self.get_tool(tool)
    action = dict(self.default_action.items() + cache["actions"][action_name].items())

    if "session" in params:
      session_id = self.get_session(params["session"])
    else:
      session_id = self.get_session()

    if action["require_auth"]:
      if not self.authorize(session_id):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm=\"Ephorize\"')
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write("Access Denied")
        raise Exception("Authentication failure")

    if "root" not in action["subsequent_actions"] and action_name != "root":
      action["subsequent_actions"].append("root")

    uri = "/%s/%s" % (tool, "root")
    form = "<form action=\""+ uri +"\" method=\"POST\">\n"
    form += "  <p>" + action["long_text"] + "</p>\n"

    print "SESSION IS " + session_id
    self.session_id = session_id
    if "job_id" in params:
      saved_job_id = params["job_id"]
    else:
      saved_job_id = ""
    job_id = str(uuid.uuid4())
    self.set_session_var(session_id, ['jobs', job_id, 'running'], False)
    self.set_session_var(session_id, ['jobs', job_id, 'action'], action_name)
    pprint(self.sessions)
    form += "  <input type=\"hidden\" name=\"session\" value=\""+session_id+"\"/>\n"
    form += "  <input type=\"hidden\" name=\"job_id\" value=\""+job_id+"\"/>\n"
    form += "  <input id=\"next_action\" type=\"hidden\" name=\"action\" value=\""+action_name+"\"/>\n"
    form += "  <input id=\"perform_action\" type=\"hidden\" name=\"run\" value=\"false\"/>\n"

    for field in action["fields"]:
      if field not in params:
        params[field] = ""
      if "fields" in cache and field in cache["fields"] and "label" in cache["fields"][field]:
        label = cache["fields"][field]["label"]
      else:
        label = field
      form += "  <label for=\""+field+"\">"+label+"<input type=\"text\" name=\""+field+"\" value=\""+params[field]+"\"/>\n"
    form += "<br/>\n"

    if action["command"]:
      form += "  <input type=\"button\" onclick=\"document.getElementById('perform_action').value='true'; form.submit();\" value=\"Ok\"/>\n"

    for subsequent in action["subsequent_actions"]:
      form += "  <input type=\"button\" onclick=\"document.getElementById('next_action').value='"+subsequent+"'; form.submit()\" value=\""+cache["actions"][subsequent]["short_text"]+"\"/>\n"

    form += "</form>\n"

    page = self.default_template.format( 
      tool = tool,
      title = "%s/%s" % (tool, action_name), 
      form = form, 
      result = "",
      session_id = self.session_id,
      job_id = saved_job_id,
      uri = uri
    )
    self.send_response(200)
    self.send_header("Content-type", "text/html")
    self.end_headers()
    self.wfile.write(page)

  def build_run_command(self,data):
    tool = self.match.group("tool")
    action_name = data["action"]
    cache = self.get_tool(tool)
    action = dict(self.default_action.items() + cache["actions"][action_name].items())
    command = [ os.curdir + "/tools/" + tool ]
    for cursor in cache, action:
      if "args" in cursor:
        for arg in cursor["args"].split():
          command.append(arg)

    for field in action["fields"]:
      command.append( "--" + field )
      if field in data:
        command.append( data[field] )
      else:
        command.append("")

    queue = Queue.Queue()
    job = subprocess.Popen( command, shell = False, stdout = subprocess.PIPE, bufsize = 0 )
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'process'], job)
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'running'], True)
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'queue'], queue)

    def _command_thread():
      user = self.get_session_var(self.session_id, [ 'auth', 'user' ])
      syslog.syslog("[%s] User %s started command: %s" % (data['job_id'], user, " ".join(command)))

      line = job.stdout.readline().rstrip()
      queue.put(json.dumps( { 'event': 'command', 'data': " ".join(command) } ));
      while line:
        queue.put(line)
        #print "LINE: %s" % line
        event_data = json.loads(line)
        if "log" in event_data:
          syslog.syslog("[%s] LOG: %s" %(data['job_id'], event_data['data']))

        line = job.stdout.readline().rstrip()
      
      returncode = job.wait()
      syslog.syslog("[%s] command completed with return-code %d" % (data['job_id'], returncode))
      print "Script terminated"
      queue.put("terminate")
      self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'running'], False)

    thread = threading.Thread(target = _command_thread)
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'thread'], thread)
    thread.start()

  def do_common(self):
    self.match = self.path_regex.match(self.path)
    if not self.match:
      self.send_error(404, 'Path not found: %s' % self.path)
      return False
    if self.match.group("tool") not in self.tools and not self.match.group("tool") in os.listdir(os.curdir+"/tools/"):
      self.send_error(404, 'Tool not found: %s' % self.match.group("tool"))
      return False
    return True
    
  def do_POST(self):
    if not self.do_common():
      return
    data_length = int(self.headers["Content-Length"])
    post_data_raw = self.rfile.read(data_length).decode('utf-8')
    print "POST: '" + post_data_raw + "'"
    post_data = urlparse.parse_qs(post_data_raw)
    data = {}
    for key,values in post_data.items():
      data[key] = values.pop()
    pprint(data)

    pprint(data)
    print "POST %s" % self.path
    self.display_action(self.match.group("tool"), data["action"], data)
    print "SESSION: " + data["session"]

    if data["run"] == "true":
      self.build_run_command(data)


  def do_static(self):
    path = "static/" + self.match.group("action")
    if self.match.group("arguments") is not None:
      path += "/" + self.match.group("arguments")
    try:
      if not os.path.abspath(path).startswith(os.path.abspath("static")):
        raise IOError("No escape")
      with open(path,'r') as content_file:
        stat = os.fstat(content_file.fileno()) 
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path))
        self.send_header('Content-Length', str(stat[6]))
        self.send_header('Last-Modified', strftime("%a, %d %b %Y %H:%M:%S %Z", localtime(stat.st_mtime)))
        self.end_headers()
        shutil.copyfileobj(content_file,self.wfile)
    except IOError:
      self.send_error(404, "File not found")
      return None
    
    pass

  def do_event(self):
    session_id = self.get_session(self.match.group("action"))
    jobid = self.match.group("arguments")
    try:
      queue = self.get_session_var(session_id,["jobs",jobid,"queue"])
    except KeyError, e:
      self.send_error(404, "No such event")
      return

    self.send_response(200)
    self.send_header('content-type', 'text/event-stream')
    self.end_headers()
    while self.get_session_var(session_id,["jobs",jobid,"running"]):
      item = queue.get()
      queue.task_done()
      if item == "terminate":
        break
      self.wfile.write(u"event: message\nid: 1\ndata: %s\ndata:\n\n" % item)
    self.wfile.write(u"event: message\nid: 1\ndata: { \"data\": [], \"event\": \"finished\" ] }\n\n")

  def do_HEAD(self):
    if not self.do_common():
      return
    self.send_response(200)
    return

  def do_GET(self):  
    if not self.do_common():
      return
    if self.match.group("tool") in self.tools:
      tool = self.tools[self.match.group("tool")]
      tool(self)
    else:
      if self.match.group("action"):
        self.display_action(self.match.group("tool"), self.match.group("action"))
      else:
        self.display_action(self.match.group("tool"), "root")

config = ConfigParser.RawConfigParser()
config.add_section("main")
config.set("main","port",8443)
config.set("main","ssl_cert","self_signed_server.pem")
config.add_section("Auth")
config.set("Auth","uri","ldaps://localhost")
config.set("Auth","domain","Local")
config.set("Auth","mode","Simple")
config.set("Auth","username", "admin")
config.set("Auth","password", "ephorize")
config.read(os.curdir + "/conf/ephorize.conf")
pprint(config)

Handler = NSAutoHTTPRequestHandler
ThreadingSimpleServer.allow_reuse_address = True

httpd = ThreadingSimpleServer(("",int(config.get("main","port"))), Handler)
httpd.socket = ssl.wrap_socket( httpd.socket, certfile=config.get("main","ssl_cert"), server_side=True )

tools = {}
tools['event'] = Handler.do_event
tools['static'] = Handler.do_static


Handler.set_tools( tools )
if config.has_section("Auth"):
  auth_mode = config.get("Auth","mode")
  if auth_mode == "AD":
    Handler.auth_module = ActiveDirectoryAuth(config.get("Auth","uri"), config.get("Auth","domain"))
  else:
    Handler.auth_module = SimpleAuth(config.get("Auth","username"),config.get("Auth","password"))

print "serving at port", config.get("main","port")
syslog.openlog("ephorize", logoption = syslog.LOG_PID)
try:
  while True:
    sys.stdout.flush()
    httpd.handle_request()
except KeyboardInterrupt:
  print "Finished"
