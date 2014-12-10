var source = new EventSource('/event/' + session_id + '/' + job_id);
source.addEventListener("message", eventHandler, false);
var indexes = {};
var pending = {};
var cursor = {};
indexes.progress = 0;
indexes.pending = 0;

function object_as_ul_recursive(item,indent,tag) {
  var padding = "";
  for ( var i = 0; i++; i < indent ) {
    padding += "  ";
  }
  output = padding + "<ul class=\""+tag+"\" id="+(tag || "_level_" + indent)+">\n";
  if ( typeof item === 'object' ) {
    var item_keys = Object.keys(item);
    var item_keys_length = item_keys.length;
    item_keys.sort();
    for ( var item_i = 0; item_i < item_keys_length; item_i++ ) {
      var key = item_keys[item_i];
      var value = item[key];
      if ( typeof value === 'object' ) {
        output += padding + "  <li> <em>" + key + "</em>\n";
        output += object_as_ul_recursive(value, indent + 1);
        output += padding + "  </li>\n";
      } else {
        output += padding + "  <li> <ul id='_level_"+(indent+1)+"_item'> <li id='key'>"+key + "</li> <li id='value'>" + value + " </li></ul></li>\n";
      }
    }
  } else {
    output += padding + "  <li> " + value + " </li>\n";
  }
  output += padding + "</ul>\n";
  return output;
}

function eventHandler(event) {
  data = JSON.parse(event.data);
  var output = ""; 
  switch(data.event) {
  case 'progress_start':
    output = data.data[0] + "\n";
    output += "<progress id='progress_bar_" + indexes.progress + "' value='0' max='100'></progress> <br/>\n";
    break;
  case 'progress':
    var progress_total = 100.0 * data.data[0] / data.data[1];
    document.querySelector('#progress_bar_' + indexes.progress.toString()).value = progress_total;
    document.querySelector('#progress_bar_' + indexes.progress.toString()).innerHTML = data.data[2];
    break;
  case 'progress_end':
    document.querySelector('#progress_bar_' + indexes.progress.toString()).value = 100;
    indexes.progress++;
    break;
  case 'data':
    indexes.data_tags = {};
    indexes.data_tags[data.data[0]] = indexes.data_tags[data.data[0]] || 0;
    output += object_as_ul_recursive(data.data[1],0,data.data[0] + indexes.data_tags[data.data[0]]);
    break;
  case 'finished':
    source.close();
    break;
  case 'pending_action_start':
    pending[indexes.pending] = {};
    pending[indexes.pending]['actions'] = {};
    pending[indexes.pending]['effects'] = {};
    break;
  case 'action_context':
    cursor['action'] = {};
    cursor['action_context'] = 0;
    pending[indexes.pending]['actions'][data.data[0]] = cursor['action'];
    break;
  case 'action':
    cursor['action'][cursor['action_context']] = data.data[0];
    cursor['action_context']++;
    break;
  case 'effect_start':
    cursor['severity'] = {};
    break;
  case 'effect_context':
    cursor['effect'] = {};
    cursor['effect_context'] = 0;
    pending[indexes.pending]['effects'][data.data[0]] = cursor['effect'];
    break;
  case 'effect':
    var severity = data.data[0];
    cursor['effect'][severity] = cursor['effect'][severity] || {};
    cursor['effect'][severity][cursor['effect_context']] = data.data[1];
    cursor['effect_context']++;
    break;
  case 'effect_end':
    output += object_as_ul_recursive(pending[indexes.pending],0,'actions_and_effects'+indexes.pending);
    indexes.pending++;
    break;
  default:
    //output += "<strong>" + event + "</strong>" + JSON.stringify(data) + "<br/>\n";
  }
  document.querySelector('#result').innerHTML += output
}

