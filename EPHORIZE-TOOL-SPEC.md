Ephorize
===

Ephorize provides a web-based workflow for administrative command-line tasks. Pretty much any task can be added to Ephorize - it expects such tasks to conform to a specification, but it is trivial to wrap arbitrary tools in a bit of shell script that provides the specified options.

The Ephorize Specification
====

Tools for Ephorize are placed in the tools/ directory. When first accessed (and on the first access every ten minutes afterwards), the server calls the tool program with the options --dump-ui-options. This is expected to dump a UI and workflow specification, in JSON. For instance, the netscaler servicegroup script:

```json
ccooke@haematite:~$ git/ephorize/bin/nsauto.py --dump-ui-options
{ 
  "fields": {
    "pattern": {
      "label": "Pattern to select Service Groups"
    },
    "enable": {
      "label": "Pattern for servers to enable"
    },
    "hostname": {
      "behaviour": "select_list",
      "label": "Select a netscaler"
    },
    "disable": {
      "label": "Pattern for servers to disable"
    }
  },
  "args": "--output-mode event",
  "actions": {
    "enable_apply": {
      "fields": [
        "hostname",
        "pattern",
        "enable"
      ],
      "short_text": "Commit the changes",
      "args": "--commit --no-confirm",
      "long_text": ""
    },
    "search": {
      "fields": [
        "hostname",
        "pattern"
      ],
      "short_text": "Display the current state of one or more servicegroups",
      "long_text": "",
      "subsequent_actions": [
        "enable_test",
        "disable_test"
      ]
    },
    "enable_test": {
      "fields": [
        "hostname",
        "pattern",
        "enable"
      ],
      "short_text": "Enable some servers",
      "long_text": "",
      "subsequent_actions": [
        "enable_apply"
      ]
    },
    "disable_apply": {
      "fields": [
        "hostname",
        "pattern",
        "disable"
      ],
      "short_text": "Commit the changes",
      "args": "--commit --no-confirm",
      "long_text": ""
    },
    "disable_test": {
      "fields": [
        "hostname",
        "pattern",
        "disable"
      ],
      "short_text": "Disable some servers",
      "long_text": "",
      "subsequent_actions": [
        "disable_apply"
      ]
    },
    "select_netscaler": {
      "fields": [
        "hostname"
      ],
      "short_text": "Choose a netscaler to work on",
      "command": false,
      "subsequent_actions": [
        "search"
      ]
    },
    "root": {
      "subsequent_actions": [
        "select_netscaler"
      ],
      "short_text": "Return to the root menu",
      "command": false,
      "long_text": ""
    }
  },
  "form": {
    "pattern": "string",
    "enable": "string",
    "disable": "string"
  }
}
ccooke@haematite:~$ 
```

Every tool must at least define a root action. This will be displayed by default when hitting the tool's web page. Other than that, tools which accept long-form command options will be easier to add - the command is built up from the args fields at action and root level, plus each of the fields in the current action, each appended as a long-option and value pair. 

Command Output
####

When an action is submitted (With the 'ok' button), Ephorize will run the command in the background and display its output in the web UI. The command can output JSON events of the form ```{ "event": "$NAME", "data": [ $ARGUMENTS ] }```. Alternatively, the script can output simple strings and Ephorize will convert these to "shell_output" events to send to the browser.


