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
from pprint import pprint
from time import time, strftime, localtime

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
    "subsequent_actions": [ "root" ]
  }
  default_template = """
    <html>
      <head>
        <title>{title}</title>
      </head>
      <body>
        <div id="result"></div>
        <div id="form">{form}</div>
        <script type="text/javascript">
          var session_id = "{session_id}";
          var job_id = "{job_id}";
        </script>
        <script type="text/javascript" src="/static/js/events.js"></script>
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
      if tool not in self.tool_cache.keys() or time() >= self.tool_cache[tool]["expiry"]:
        # re-cache the tool data
        process = subprocess.Popen([ self.tools[tool], "--dump-ui-options"], stdout = subprocess.PIPE)
        data = json.load( process.stdout )
        self.tool_cache[tool] = {
          'cache': data,
          'expiry': time() + 600,
        }
        print "Updated the tool cache for %s" % tool
    finally: 
      self.cache_lock.release()
    return self.tool_cache[tool]['cache']

  def get_session(self,session_id = None):
    try:
      self.session_lock.acquire()
      now = time()
      for session in self.sessions.keys():
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

  def display_action(self,tool,action_name,params={}):
    cache = self.get_tool(tool)
    action = dict(self.default_action.items() + cache["actions"][action_name].items())

    if "root" not in action["subsequent_actions"] and action_name != "root":
      action["subsequent_actions"].append("root")

    uri = "http://localhost:8000/%s/%s" % (tool, "root")
    form = "<form action=\""+ uri +"\" method=\"POST\">\n"
    form += "  <p>" + action["long_text"] + "</p>\n"

    if "session" in params.keys():
      print "RESTORE SESSION " + params["session"]
      session_id = self.get_session(params["session"])
    else:
      pprint(params.keys())
      session_id = self.get_session()
    print "SESSION IS " + session_id
    self.session_id = session_id
    if "job_id" in params.keys():
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
      if "fields" in cache.keys() and field in cache["fields"].keys() and "label" in cache["fields"][field].keys():
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
    command = [ self.tools[tool] ]
    for cursor in cache, action:
      if "args" in cursor.keys():
        for arg in cursor["args"].split():
          command.append(arg)

    for field in action["fields"]:
      command.append( "--" + field )
      if field in data.keys():
        command.append( data[field] )
      else:
        command.append("")

    queue = Queue.Queue()
    job = subprocess.Popen( command, shell = False, stdout = subprocess.PIPE, bufsize = 0 )
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'process'], job)
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'running'], True)
    self.set_session_var(self.session_id, [ 'jobs', data['job_id'], 'queue'], queue)

    def _command_thread():
      print "COMMAND: %s" % command

      line = job.stdout.readline().rstrip()
      queue.put(json.dumps( { 'event': 'command', 'data': " ".join(command) } ));
      while line:
        queue.put(line)
        #print "LINE: %s" % line
        line = job.stdout.readline().rstrip()
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
    if self.match.group("tool") not in self.tools.keys():
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
      if item == "terminate":
        queue.task_done()
        return
      self.wfile.write(u"event: message\nid: 1\ndata: %s\ndata:\n\n" % item)
      queue.task_done()

  def do_HEAD(self):
    if not self.do_common():
      return
    self.send_response(200)
    return

  def do_GET(self):  
    if not self.do_common():
      return
    print( "GET %s" % self.path )
    tool = self.tools[self.match.group("tool")]
    if type(tool) == str:
      if self.match.group("action"):
        self.display_action(self.match.group("tool"), self.match.group("action"))
      else:
        self.display_action(self.match.group("tool"), "root")
    elif callable(tool):
      tool(self)
    
PORT = 8000

Handler = NSAutoHTTPRequestHandler
ThreadingSimpleServer.allow_reuse_address = True

httpd = ThreadingSimpleServer(("",PORT), Handler)

Handler.set_tools( 
  { 
    'nsauto': '/home/ccooke/bin/nsauto',
    'event': Handler.do_event,
    'static': Handler.do_static
  } 
)

print "serving at port", PORT
try:
  while True:
    sys.stdout.flush()
    httpd.handle_request()
except KeyboardInterrupt:
  print "Finished"
