#!/usr/bin/python

from nsnitro import *
from pprint import pprint
import os
import os.path
import json
import fnmatch
import sys
import re
import time
import copy
import fcntl

class NetscalerAuthFromFile:
  def __init__(self,hostname):
    self.hostname = hostname
    auth_hash = self.get_auth_hash()

  def get_auth_hash(self):
    attempt_dirs = [
      "%s/.nsauto" % os.environ["HOME"],
      "/etc/nsauto"
    ]
    
    def _generate_files(hostname):
      output = []
      filename = "db"
      for word in reversed(hostname.split(".")):
        filename = "%s.%s" % (word, filename)
        output.append(filename)
      output.append("default.pw")
      return output
    attempt_files = []
    for directory in attempt_dirs:
      for filename in _generate_files(self.hostname):
        path = "%s/%s" %(directory,filename)
        if os.path.isfile(path):
          try:
            data = json.load(open(path,"r"))
            self.username = data["username"]
            self.password = data["password"]
            return
          except Exception as e:
            print("Failed to open %s: %s" % (filename, e))
            pass
    raise Exception("No credentials found")

class NetscalerAutomation:
  def __init__(self,hostname,output_handler):
    self.auth = NetscalerAuthFromFile(hostname)
    self.current_state = {}
    self.output = NetscalerAutomationOutputHandler.get(output_handler)
    self.init_connection()
    for key in ["servicegroup"]:
      self.current_state[key] = {}

  def dump(self,tag,obj):
    self.output.data(
      tag,
      strip_object(obj),
    )

  def init_connection(self):
    self.nitro = NSNitro(self.auth.hostname,self.auth.username,self.auth.password, useSSL = True)
    try:
      self.nitro.login()
    except Exception as e:
      self.output.fatal(
        "Unable to login on %s@%s: %s" %( 
          self.auth.username,
          self.auth.hostname,
          e
        )
      )

  def find_servicegroups(self,pattern = ""):
    pattern += "*"
    self.init_connection()
    temp = {}
    count = 0
    self.output.progress_start("Searching for servicegroups matching \"%s\"" % pattern)
    service_groups = NSServiceGroup.get_all(self.nitro)
    for svg in service_groups:
      count += 1
      svg_hash = {
        "servers": {},
        "__obj": svg
      }
      name = svg.get_servicegroupname()
      self.current_state["servicegroup"][name] = {
        "up": 0,
        "enabled": 0,
        "count": 0
      }
      current_state = self.current_state["servicegroup"][name]
      if not fnmatch.fnmatch(name,pattern):
        continue
      temp[name] = svg_hash
      self.output.progress(count,len(service_groups),name)
      for server in NSServiceGroup.get_servers(self.nitro,svg):
        identifier = "%s:%d" % (server.get_servername(),server.get_port())
        state = server.get_svrstate()
        svg_hash["servers"][identifier] = {
          "enabled": (state != "OUT OF SERVICE"),
          "up": (state == "UP"),
          "weight": int(server.get_weight()),
          "__obj": server
        }
        current_state["count"] += 1
        for key in ["up", "enabled"]:
          if svg_hash["servers"][identifier][key]:
            current_state[key] += 1
    self.output.progress_end()
    return temp

  def begin_change(self):
    self.expected_state = copy.deepcopy(self.current_state)  
    self.changed = {}
    self.actions = []
    #self.output.begin_change()

  def toggle_server_in_servicegroup(self,server,groups,enable=False,disable=False):
    server += "*"
    if enable and disable:
      raise Exception("Make your mind up (and don't try to enable and disable the same server)")
    for svg_name,svg_tree in groups.iteritems():
      expected_state = self.expected_state["servicegroup"][svg_name]
      for identifier,server_tree in svg_tree["servers"].iteritems():
        if fnmatch.fnmatch(identifier,server):
          if enable and not server_tree["enabled"]:
            action = [ "enable_server", svg_tree["__obj"], server_tree["__obj"] ]
            if action not in self.actions:
              self.actions.append( action )
              expected_state["enabled"] += 1
              expected_state["changed"] = True
          elif disable and server_tree["enabled"]:
            action = [ "disable_server", svg_tree["__obj"], server_tree["__obj"] ]
            if action not in self.actions:
              self.actions.append( action )
              if server_tree["up"]:
                expected_state["up"] -= 1
              expected_state["enabled"] -= 1 
              expected_state["changed"] = True

  def print_effects(self):
    if len(self.actions) == 0:
      return
    self.output.pending_action_start()
    
    last_context = None
    for action in sorted(self.actions):
      if action[0] == "enable_server":
        context = action[2].get_servicegroupname()
        if context != last_context:
          self.output.action_context(context)
          last_context = context
        self.output.action("The server %s:%d will be enabled" % (action[2].get_servername(), action[2].get_port()))
      elif action[0] == "disable_server":
        context = action[2].get_servicegroupname()
        if context != last_context:
          self.output.action_context(context)
          last_context = context
        self.output.action("The server %s:%d will be disabled" % (action[2].get_servername(), action[2].get_port()))

    self.output.effect_start()

    for obj in self.expected_state.keys():
      for name,expected in self.expected_state[obj].iteritems():
        if "changed" not in expected.keys():
          continue
        current = self.current_state[obj][name]
        self.output.effect_context("%s/%s" % (obj,name))
        difference = expected["enabled"] - current["enabled"]
        if expected["enabled"] == 0 and difference != 0:
          self.output.effect("ALERT","ALL servers will be DISABLED")
        elif difference == 0:
          self.output.effect("INFO","The number of enabled servers will be unchanged")
        elif difference > 0:
          self.output.effect("INFO","The number of enabled servers will increase by %d to %d" % (difference, expected["enabled"]))
        elif difference < 0:
          self.output.effect("WARNING","The number of enabled servers will decrease by %d to %d" % (difference, expected["enabled"]))
        if expected["up"] < current["up"]:
          delta = current["up"] - expected["up"]
          self.output.effect("WARNING","%d ACTIVE server(s) will be DISABLED"%(delta))

    self.output.effect_end()

  def finished(self):
    self.logout()
    self.output.finished()

  def commit(self):
    self.output.progress_start("Applying changes...")
    count = 0
    for action in self.actions:
      count += 1
      if action[0] == "enable_server":
        action[1].enable_server(self.nitro,action[2])
        self.output.progress(count,len(self.actions),"Enabling %s:%d" % (action[2].get_servername(), action[2].get_port()), action[1].get_servicegroupname() )
      elif action[0] == "disable_server":
        action[1].disable_server(self.nitro,action[2])
        self.output.progress(count,len(self.actions),"Disabling %s:%d" % (action[2].get_servername(), action[2].get_port()), action[1].get_servicegroupname() )
    self.output.progress_end()

  def logout(self):
    self.nitro.logout()

class NetscalerAutomationOutputHandler:
  def __init__(self):
    fl = fcntl.fcntl(sys.stdout.fileno(), fcntl.F_GETFL)
    fl |= os.O_SYNC
    fcntl.fcntl(sys.stdout.fileno(), fcntl.F_SETFL, fl)

  @staticmethod
  def get(handler):
    handlers = {
      "event": NetscalerAutomationOutputHandler(),
      "cli": NetscalerAutomationOutputHandlerCLI(),
      "cli_dumb": NetscalerAutomationOutputHandlerCLIDumb(),
#      "web": NetscalerAutomationOutputHandlerWeb(),
    }
    return handlers[handler]

  def fatal(self,message):
    print("FATAL %s" % message)
    exit(2)
  
  def confirm(self,message):
    self.confirm_message(message)
    line = sys.stdin.readline()
    if re.match("^ok$",line):
      return True
    else:
      return False

  def __getattr__(self,name):
    def _missing(*args, **keys):
      tmp = {
          'event': name,
          'data': args
      }
      if "log" in keys and keys["log"]:
        tmp['log'] = True

      print json.dumps(tmp)
      sys.stdout.flush()
    return _missing

class NetscalerAutomationOutputHandlerCLI(NetscalerAutomationOutputHandler):
  def __init__(self):
    self.red = "\033[31;1m"
    self.green ="\033[32;1m"
    self.yellow = "\033[33;1m"
    self.blue = "\033[34;1m"
    self.purple = "\033[35;1m"
    self.white ="\033[1m"
    self.default = "\033[0m"

  def get_colour(self,colour):
    self.colours[colour]
    
  def progress_start(self,message):
    sys.stdout.write("  %s\n  [%40s] starting..." % (message, ""))
    sys.stdout.flush()

  def progress(self,current,total,message,heading_message=""):
    percent = 40.0 * current / total
    sys.stdout.write("\033[4G%-40s\033[46G%s\033[J" % ("#" * int(percent), message))
    sys.stdout.flush()

  def progress_end(self):
    print("\033[4G%40s\033[46GCompleted.\033[J" % ("#"*40))

  def data(self,tag,data):
    for svg_name in sorted(data.keys()):
      svg = data[svg_name]
      print("Service Group %s%s%s" % (self.white,svg_name,self.default))
      for svr_name in sorted(svg["servers"].keys()):
        svr = svg["servers"][svr_name]
        state = []
        if svr["enabled"]:
          state.append("%senabled%s " % (self.blue,self.default) )
        else:
          state.append("%sdisabled%s" % (self.purple,self.default) )

        if svr["up"]:
          state.append(" %sup%s " %(self.green,self.default) )
        else:
          state.append("%sdown%s" %(self.red,self.default) )

        print("  %-42s [%s] with weight %d" % (svr_name, ",".join(state), svr["weight"]) )

  def pending_action_start(self):
    print("/---------------------------------------")
    print("| Actions pending:                     ")
    print("|---------------------------------------")

  def action_context(self,context):
    print("|--- In %s%s%s %s" %(self.white,context, self.default,"-" * (40 - len(context) - 9) ))

  def action(self,action):
    print("| %s" % action)

  def effect_start(self):
    print("|---------------------------------------")
    print("| If these actions are committed:      ")
    print("|---------------------------------------")

  def effect_context(self,context):
    print("|--- In %s%s%s %s" %(self.white,context,self.default, "-" * (40 - len(context) - 9) ))

  def effect(self,severity,effect):
    severity_colour = {
      "ALERT": self.red,
      "INFO": "",
      "WARNING": self.yellow
    }
    print("| * %s%s%s" %( severity_colour[severity], effect, self.default ) )

  def effect_end(self):
    print("\\---------------------------------------")

  def confirm_message(self,message):
    print(message)
    print("Type \"ok\" to continue or Ctrl-C to abort")

class NetscalerAutomationOutputHandlerCLIDumb(NetscalerAutomationOutputHandlerCLI):
  def __init__(self):
    self.white = ""
    self.red = ""
    self.green = ""
    self.blue = ""
    self.yellow = ""
    self.purple = ""
    self.default = ""

  def progress_start(self,message):
    print(message)
    sys.stdout.write("|#")
    sys.stdout.flush()
    self.progress_current = 1

  def progress(self,current,total,message,extra=None):
    percent = int(50.0 * current / total)
    if percent > self.progress_current:
      delta = percent - self.progress_current
      sys.stdout.write("#" * delta)
      self.progress_current = percent
      sys.stdout.flush()

  def progress_end(self):
    print("| Completed")

class SaveResult:
  def store(self,value):
    self.get = value
    return value

def strip_object(obj):
  _type = type(obj)

  if _type in [ unicode, str, int, bool ]:
    return obj
  elif _type == dict:
    temp = {}
    for key,value in obj.iteritems():
      try:
        temp[strip_object(key)] = strip_object(value)
      except TypeError:
        pass
  elif _type == list:
    temp = []
    for value in obj:
      try:
        temp.append(strip_object(value))
      except TypeError:
        pass
  else:
    raise TypeError("Invalid type: %s" % _type)

  return temp

def display_help():
  print """Usage: nsauto [options]
  Options:
    OPTION          PARAM     EFFECT
    --hostname, -H  hostname  - Connect to this NS host
    --help, -h                - Display this help text
    --pattern, -p   pattern   - A pattern to match against servicegroups
    --enable, -e    pattern   - A patterm to match against servers to enable
    --disable, -d   pattern   - A patterm to match against servers to disable
    --(no-)confirm            - Do (not) require confirmation before taking
                                any actions. Enabled by default.
    --(no-)commit             - Do (not) actually perform actions, rather than 
                                display what would be changed. Disabled by 
                                default
    --(no-)status             - Do (not) display the current state
    --output-mode   mode      - Choose an output mode. Available are "cli",
                                "cli_dumb" and "event".

    Authentication to the netscaler is perfomed by username and password.
    the script looks for /etc/nsauto and $HOME/.nsauto, looking for credentials
    files. Given the hostname "nsr1.internal.example", it will look for
    the following files: "example.pw", "internal.example.pw", 
    "nsr1.internal.example.pw" and "default.pw" (if everything else fails)
    The file should contain a single-line of JSON of the form: 
      { "username": "nsroot", "password": "<elided>"}

  Examples:
    Display the state of servicegroups matching "*-market2*"

      nsauto --hostname nsr1.internal.example --pattern "*-market2*"

    Enable beweb4 in all servicegroups for market2:

      nsauto --hostname nsr1.internal.example --pattern "*-market2.*" --enable "beweb4"

    Disable bemw2 on port 8877 for market4:

      nsauto --hostname nsr1.internal.example --pattern "*-market4*:8877" --disable "bemw2"
"""

def dump_ui_options():
  print( 
    json.dumps( 
      {
        "form": {
          "pattern": "string",
          "enable": "string",
          "disable": "string"
        },
        "fields": {
          "hostname": {
            "label": "Select a netscaler",
            "behaviour": "select_list"
          },
          "pattern": { "label": "Pattern to select Service Groups" },
          "enable": { "label": "Pattern for servers to enable" },
          "disable": { "label": "Pattern for servers to disable" },
        },
        "args": "--output-mode event",
        "actions": {
          "root": {
            "short_text": "Return to the root menu",
            "long_text": "",
            "command": False,
            "require_auth": False,
            "subsequent_actions": [ "select_netscaler" ],
          },
          "select_netscaler": {
            "short_text": "Choose a netscaler to work on",
            "fields": [ "hostname" ],
            "command": False,
            "require_auth": False,
            "subsequent_actions": [ "search" ],
          },
          "search": {
            "short_text": "Display the current state of one or more servicegroups",
            "long_text": "",
            "fields": [ "hostname", "pattern" ],
            "subsequent_actions": [ "enable_test", "disable_test" ]
          },
          "enable_test": {
            "short_text": "Enable some servers",
            "long_text": "",
            "fields": [ "hostname", "pattern", "enable" ],
            "subsequent_actions": [ "enable_apply", "disable_test", "search" ],
            "args": "--no-status",
          },
          "enable_apply": {
            "short_text": "Commit the changes",
            "long_text": "",
            "fields": [ "hostname", "pattern", "enable" ],
            "args": "--commit --no-confirm --no-status",
            "subsequent_actions": [ "disable_test", "enable_test", "search" ],
          },
          "disable_test": {
            "short_text": "Disable some servers",
            "long_text": "",
            "fields": [ "hostname", "pattern", "disable" ],
            "subsequent_actions": [ "disable_apply", "enable_test", "search" ],
            "args": "--no-status",
          },
          "disable_apply": {
            "short_text": "Commit the changes",
            "long_text": "",
            "fields": [ "hostname", "pattern", "disable" ],
            "args": "--commit --no-confirm --no-status",
            "subsequent_actions": [ "disable_test", "enable_test", "search" ],
          }
        }
      },
      indent=2
    )
  )

#servers[3].enable_server(nitro, servers[3])
class ShortOpt:
  def __init__(self,longopt):
    self.longopt = longopt

opts = {
  "output-mode": "cli",
  "dump-ui-options": False,
  "hostname": "localhost",
  "H": ShortOpt("hostname"),
  "pattern": None,
  "p": ShortOpt("pattern"),
  "enable": [],
  "e": ShortOpt("enable"),
  "disable": [],
  "d": ShortOpt("disable"),
  "help": False,
  "h": ShortOpt("help"),
  "commit": False,
  "confirm": True,
  "status": True,
}
if not sys.stdout.isatty():
  opts["output-mode"] = "cli_dumb"

match = SaveResult()
cursor = 1
while cursor < len(sys.argv) and match.store(re.match("^(?:--(?P<negated>no-)?(?P<longopt>[-\w]+)|-(?P<shortopt>\w))$",sys.argv[cursor])):
  if match.get.group("shortopt"):
    key = match.get.group("shortopt")
    if key in opts and opts[key].__class__ == ShortOpt:
      key = opts[key].longopt
    else:
      raise Exception("Invalid short option %s" % key )
  else:
    key = match.get.group("longopt")
  
  if type(opts[key]) == bool:
    if match.get.group("negated") is None:
      opts[key] = True
    else:
      opts[key] = False
  elif type(opts[key]) == list:
    cursor += 1
    opts[key].append(sys.argv[cursor])
  else:
    cursor += 1
    opts[key] = sys.argv[cursor]
  cursor += 1

if opts["dump-ui-options"]:
  dump_ui_options()
  exit(0)

if opts["help"]: 
  display_help()
  exit(0)

api = NetscalerAutomation(opts["hostname"],opts["output-mode"])

if opts["pattern"] is None:
  exit(0)

# Stage 1: Print the current state
svg_list = api.find_servicegroups(opts["pattern"])
if opts["status"]:
  api.dump("service_group_list",svg_list)

api.begin_change()

# Stage 2: Make the changes
for server in opts["enable"]:
  api.toggle_server_in_servicegroup(server,svg_list,enable=True)
for server in opts["disable"]:
  api.toggle_server_in_servicegroup(server,svg_list,disable=True)

api.print_effects()

if len(api.actions) == 0:
  api.finished()
  exit(0)

if opts["commit"] == False:
  exit(0)

if opts["confirm"]:
  if not api.output.confirm("Please confirm these changes before we commit them"):
    api.finished()
    exit(3)

api.commit()

if not opts["status"]:
  exit(0)

time.sleep(1)
new_svg_list = api.find_servicegroups(opts["pattern"])
api.dump("service_group_list_after",new_svg_list)
api.finished()
