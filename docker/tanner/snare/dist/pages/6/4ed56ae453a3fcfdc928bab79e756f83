/* Redmine - project management software
   Copyright (C) 2006-2017  Jean-Philippe Lang */

var draw_gantt = null;
var draw_top;
var draw_right;
var draw_left;

var rels_stroke_width = 2;

function setDrawArea() {
  draw_top   = $("#gantt_draw_area").position().top;
  draw_right = $("#gantt_draw_area").width();
  draw_left  = $("#gantt_area").scrollLeft();
}

function getRelationsArray() {
  var arr = new Array();
  $.each($('div.task_todo[data-rels]'), function(index_div, element) {
    var element_id = $(element).attr("id");
    if (element_id != null) {
      var issue_id = element_id.replace("task-todo-issue-", "");
      var data_rels = $(element).data("rels");
      for (rel_type_key in data_rels) {
        $.each(data_rels[rel_type_key], function(index_issue, element_issue) {
          arr.push({issue_from: issue_id, issue_to: element_issue,
                    rel_type: rel_type_key});
        });
      }
    }
  });
  return arr;
}

function drawRelations() {
  var arr = getRelationsArray();
  $.each(arr, function(index_issue, element_issue) {
    var issue_from = $("#task-todo-issue-" + element_issue["issue_from"]);
    var issue_to   = $("#task-todo-issue-" + element_issue["issue_to"]);
    if (issue_from.size() == 0 || issue_to.size() == 0) {
      return;
    }
    var issue_height = issue_from.height();
    var issue_from_top   = issue_from.position().top  + (issue_height / 2) - draw_top;
    var issue_from_right = issue_from.position().left + issue_from.width();
    var issue_to_top   = issue_to.position().top  + (issue_height / 2) - draw_top;
    var issue_to_left  = issue_to.position().left;
    var color = issue_relation_type[element_issue["rel_type"]]["color"];
    var landscape_margin = issue_relation_type[element_issue["rel_type"]]["landscape_margin"];
    var issue_from_right_rel = issue_from_right + landscape_margin;
    var issue_to_left_rel    = issue_to_left    - landscape_margin;
    draw_gantt.path(["M", issue_from_right + draw_left,     issue_from_top,
                     "L", issue_from_right_rel + draw_left, issue_from_top])
                   .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
    if (issue_from_right_rel < issue_to_left_rel) {
      draw_gantt.path(["M", issue_from_right_rel + draw_left, issue_from_top,
                       "L", issue_from_right_rel + draw_left, issue_to_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
      draw_gantt.path(["M", issue_from_right_rel + draw_left, issue_to_top,
                       "L", issue_to_left + draw_left,        issue_to_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
    } else {
      var issue_middle_top = issue_to_top +
                                (issue_height *
                                   ((issue_from_top > issue_to_top) ? 1 : -1));
      draw_gantt.path(["M", issue_from_right_rel + draw_left, issue_from_top,
                       "L", issue_from_right_rel + draw_left, issue_middle_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
      draw_gantt.path(["M", issue_from_right_rel + draw_left, issue_middle_top,
                       "L", issue_to_left_rel + draw_left,    issue_middle_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
      draw_gantt.path(["M", issue_to_left_rel + draw_left, issue_middle_top,
                       "L", issue_to_left_rel + draw_left, issue_to_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
      draw_gantt.path(["M", issue_to_left_rel + draw_left, issue_to_top,
                       "L", issue_to_left + draw_left,     issue_to_top])
                     .attr({stroke: color,
                          "stroke-width": rels_stroke_width
                          });
    }
    draw_gantt.path(["M", issue_to_left + draw_left, issue_to_top,
                     "l", -4 * rels_stroke_width, -2 * rels_stroke_width,
                     "l", 0, 4 * rels_stroke_width, "z"])
                   .attr({stroke: "none",
                          fill: color,
                          "stroke-linecap": "butt",
                          "stroke-linejoin": "miter"
                          });
  });
}

function getProgressLinesArray() {
  var arr = new Array();
  var today_left = $('#today_line').position().left;
  arr.push({left: today_left, top: 0});
  $.each($('div.issue-subject, div.version-name'), function(index, element) {
    var t = $(element).position().top - draw_top ;
    var h = ($(element).height() / 9);
    var element_top_upper  = t - h;
    var element_top_center = t + (h * 3);
    var element_top_lower  = t + (h * 8);
    var issue_closed   = $(element).children('span').hasClass('issue-closed');
    var version_closed = $(element).children('span').hasClass('version-closed');
    if (issue_closed || version_closed) {
      arr.push({left: today_left, top: element_top_center});
    } else {
      var issue_done = $("#task-done-" + $(element).attr("id"));
      var is_behind_start = $(element).children('span').hasClass('behind-start-date');
      var is_over_end     = $(element).children('span').hasClass('over-end-date');
      if (is_over_end) {
        arr.push({left: draw_right, top: element_top_upper, is_right_edge: true});
        arr.push({left: draw_right, top: element_top_lower, is_right_edge: true, none_stroke: true});
      } else if (issue_done.size() > 0) {
        var done_left = issue_done.first().position().left +
                           issue_done.first().width();
        arr.push({left: done_left, top: element_top_center});
      } else if (is_behind_start) {
        arr.push({left: 0 , top: element_top_upper, is_left_edge: true});
        arr.push({left: 0 , top: element_top_lower, is_left_edge: true, none_stroke: true});
      } else {
        var todo_left = today_left;
        var issue_todo = $("#task-todo-" + $(element).attr("id"));
        if (issue_todo.size() > 0){
          todo_left = issue_todo.first().position().left;
        }
        arr.push({left: Math.min(today_left, todo_left), top: element_top_center});
      }
    }
  });
  return arr;
}

function drawGanttProgressLines() {
  var arr = getProgressLinesArray();
  var color = $("#today_line")
                    .css("border-left-color");
  var i;
  for(i = 1 ; i < arr.length ; i++) {
    if (!("none_stroke" in arr[i]) &&
        (!("is_right_edge" in arr[i - 1] && "is_right_edge" in arr[i]) &&
         !("is_left_edge"  in arr[i - 1] && "is_left_edge"  in arr[i]))
        ) {
      var x1 = (arr[i - 1].left == 0) ? 0 : arr[i - 1].left + draw_left;
      var x2 = (arr[i].left == 0)     ? 0 : arr[i].left     + draw_left;
      draw_gantt.path(["M", x1, arr[i - 1].top,
                       "L", x2, arr[i].top])
                   .attr({stroke: color, "stroke-width": 2});
    }
  }
}

function drawGanttHandler() {
  var folder = document.getElementById('gantt_draw_area');
  if(draw_gantt != null)
    draw_gantt.clear();
  else
    draw_gantt = Raphael(folder);
  setDrawArea();
  if ($("#draw_progress_line").prop('checked'))
    drawGanttProgressLines();
  if ($("#draw_relations").prop('checked'))
    drawRelations();
}
