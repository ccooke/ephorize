var source = new EventSource('http://localhost:8000/event/' + session_id + '/' + job_id);
source.addEventListener("message", eventHandler, false);
var indexes = {};
indexes.progress = 0;

function object_as_ul_recursive(item,indent,tag) {
  var padding = "";
  for ( var i = 0; i++; i < indent ) {
    padding += "  ";
  }
  output = padding + "<ul id="+(tag || "_level_" + indent)+">\n";
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
        output += padding + "  <li> <ul id='_level_"+(indent+1)+"_item'> <li>"+key + "</li> <li>" + value + " </li></ul></li>\n";
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
  case 'pending_action_start':
  case 'action_content':
  case 'action':
  case 'effect_start':
  case 'effect_content':
  case 'effect':
  case 'effect_end':
  case 'data':
    indexes.data_tags = {};
    indexes.data_tags[data.data[0]] = indexes.data_tags[data.data[0]] || 0;
    output += object_as_ul_recursive(data.data[1],0,data.data[0] + indexes.data_tags[data.data[0]]);
    break;
  case 'finished':
    source.removeEventHandler("message", eventHandler);
  default:
  }
  document.querySelector('#result').innerHTML += output
}

