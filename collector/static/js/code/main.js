function toggle(id) {
  $("#" + id).toggle();
}

function toDate(dateStr) {
    const [day, month, year] = dateStr.split('.');
    return new Date(year, month-1, day);
}

function hello() {
    let start_date = $("#attack-src-filters-startD").val();
    let end_date = $("#attack-src-filters-endD").val();
    alert("" + start_date + " " + end_date);
}

$(function () {
    $('.datepicker').datepicker({ dateFormat: 'dd.mm.yy' });
    $('.stat-body').hide();
})

function setIfLess(source_id, target_id) {
    const source = $('#' + source_id);
    let target = $('#' + target_id);
    if (target.val().length === 0)
        return;
    if (source.datepicker('getDate') > target.datepicker('getDate')) {
        target.val(source.val())
    }
}

function setIfGreater(source_id, target_id) {
    const source = $('#' + source_id);
    let target = $('#' + target_id);

    if (target.val().length === 0)
        return;
    if (source.datepicker('getDate') < target.datepicker('getDate')) {
        target.val(source.val())
    }
}

// function getGraphSeries(data) {
//   return [
//         {
//           draggable: true,
//           edgeSymbol: ['none', 'arrow'],
//           type: 'graph',
//           layout: 'force',
//           force: {
//             layoutAnimation: true,
//             gravity: 0.05,
//             repulsion: 50
//           },
//
//           data: data['vertices'],
//           links: data['links'] }
//       ]
// }


function getSankeySeries(data) {
  return [
        {
          type: 'sankey',
          nodeGap: 10,
          data: data['vertices'],
          links: data['links']
        }
      ]
}

function resizeEChart(canvas) {
  window.echarts.getInstanceById(canvas.attr('_echarts_instance_')).resize();
}

function getDate(id) {
  let date = $("#" + id).val();
  if (date.length === 0) date = "none";
  return date;
}

function getPositiveInt(id) {
  let min_attacks_str = $("#" + id).val();
  if (min_attacks_str.length > 0)
    return parseInt(min_attacks_str);
  return 0;
}

function getIP(id) {
  let date = $("#" + id).val();
  if (date.length === 0)
    return "none"

  return date;
}

function makeTitle(prefix, start, end) {
  let title = prefix;
  if (start !== "none")
    title += " from " + start
  if (end !== "none")
    title += " until " + end
  else
    title += " until now"

  return title;
}

function updateAttackGraph() {
  let bg_only = $("#attack-src-filters-bg_only").prop("checked");
  let canvas = $("#attack-src-canvas")

  let start = getDate("attack-src-filters-startD");
  let end = getDate("attack-src-filters-endD");
  let minAttacks = getPositiveInt("attack-src-filters-minattacks");

  let title = makeTitle('Attacked Honeypots', start, end)
  let myChart = echarts.init(canvas.get(0));
  let option;

  let global_ip = getIP("global-filters-ip");

  canvas.show();
  myChart.showLoading();

  $.get('/attack_graph/' + start + "/" + end + "/" + minAttacks + "/" + global_ip + "/" + (bg_only ? 1 : 0), function (data) {
    myChart.hideLoading();

    if ('error' in data) {
      alert(data['error']);
      return;
    }

    if (data['vertices'].length === 0) {
      alert("No data found with these filters.")
      return;
    }

    if (data['vertices'].length > 50)
        canvas.height(data['vertices'].length*30)
    else
      canvas.height(500)



    option = {
      title: {
        subtext: title,
        left: 'center'
      },
      tooltip: {
        trigger: 'item',
        triggerOn: 'mousemove'
      },
      backgroundColor: '#fff',
      series: getSankeySeries(data),
      emphasis: {
        focus: 'adjacency'
      },
      lineStyle: {
        curveness: 0.5
      }
    };

    option && myChart.setOption(option);
    resizeEChart(canvas);

  })


}

function verifyPositiveNumber(id) {
  let element = $('#'+id);
  let value = element.val();

  if (value.length === 0 || parseInt(value) < 0)
    element.val(0)
}

function clearFilters(ids) {
    ids.forEach(function (item) {
        $('#' + item).val('');
    })
}

function capitalize(str) {
  return str.at(0).toUpperCase() + str.slice(1);
}

function parseDate(date) {
  return "" + date.getFullYear() + "-" +
    String(date.getMonth() + 1).padStart(2, '0') + "-" +
    String(date.getDate()).padStart(2, '0') + "\n" +
    String(date.getHours()).padStart(2, '0') + ":" +
    String(date.getMinutes()).padStart(2, '0')  + ":" +
    String(date.getSeconds()).padStart(2, '0');
}



function updateAttackTrend() {
  let canvas = $("#attack-trend-canvas")
  let infoPanel = $("#attack-trend-info-panel")

  let start = getDate("attack-trend-filters-startD");
  let end = getDate("attack-trend-filters-endD");
  let resolution = $("#attack-trend-filters-resolution").val();
  let title = makeTitle("Attack Trend", start, end) + " with resolution " + resolution;

  let global_ip = getIP("global-filters-ip");
  let bg_only = $("#attack-trend-filters-bg_only").prop("checked");

  let myChart = echarts.init(canvas.get(0));
  let option;

  canvas.show();
  myChart.showLoading();

  $.get('/attack_trend/' + start + "/" + end + "/" + resolution + "/" + global_ip + "/" + (bg_only ? 1 : 0), function (data) {
    canvas.height(400);
    myChart.hideLoading();


    if ('error' in data) {
      alert("Error: " + data['error']);
      return;
    }

    const markLineOpt = {
      title: title,
      animation: true,
      lineStyle: {
        color: 'red',
        type: 'solid'
      },
      data: [
        [
          {
            coord: [0, data['b']],
            symbol: 'none'
          },
          {
            coord: [data['max_x'], data['a']*data['max_x'] + data['b']],
            symbol: 'none'
          }
        ]
      ]
    };
    let start_date = Date.parse(data['first_attack'])
    let mult = 3600000;
    if (resolution === 'day') {
      mult *= 24
    } else if (resolution === 'week') {
      mult *= 24 * 7
    }

    option = {
      xAxis: {
        axisLabel: {
            formatter: function (value) {
                return parseDate(new Date(start_date + value*mult));
                // And other formatter tool (e.g. moment) can be used here.
            }
        },
        type: 'time',
        nameLocation: 'center',
        nameGap: 30,
        nameTextStyle: {
          color: "#000"
        }
      },
      yAxis: {
        name: "Number of Attacks",
        type: "value",
        nameLocation: 'center',
        nameGap: 50,
        nameTextStyle: {
          color: "#000"
        }
      },
      backgroundColor: "#fff",
      series: {
        symbolSize: 10,
        type: 'scatter',
        data: data['data'],
        markLine: markLineOpt
      }
    }
    option && myChart.setOption(option);

    resizeEChart(canvas);
    let distance = data['a']/data["sigma_a"];
    let info;

    if (Math.abs(distance) > 2) {
      info = "There is more than 97.7% chance that the number of attacks is ";
      if (distance > 0) {
        info += "<b> increasing </b>"
      } else {
        info += "<b> decreasing </b>"
      }
    } else {
      info = "There <b> isn't enough certainty </b> to determine the trend of the attack."
    }

    infoPanel.html(info);

    $("#attack-trend-param-value-a").html(Math.round(data['a']*100)/100.0)
    $("#attack-trend-param-value-b").html(Math.round(data['b']*100)/100.0)
    $("#attack-trend-param-value-sigma_a").html(Math.round(data['sigma_a']*100)/100.0)
    $("#attack-trend-parameters").show()

    infoPanel.show()
  })

}

function updateMulti() {
  let ip = $("#global-filters-ip").val()
  if (ip === "") {
    alert("Please enter up in the global filters");
    return;
  }

  let canvas = $("#attack-multi-canvas")
  let myChart = echarts.init(canvas.get(0));
  let option;

  canvas.show();
  myChart.showLoading();



  $.get("/multi_target/" + ip, function (data) {
    myChart.hideLoading();

    if ('error' in data) {
      alert("Error: " + data['error']);
      return;
    }

    let attack_start = Date.parse(data['attack_start']);
    let attack_end = Date.parse(data['attack_end']);

    let targets = Object.keys(data['data']).length;

    canvas.height(400*targets);

    let counter = 0;
    let grids = $.map(data['data'], function (val, key) {
      counter+= 1;
      return {
        top: "" + (((counter-1)*100 + 10)/targets) + "%",
        bottom: "" + (((targets - counter)*100 + 10)/targets) + "%"
      }
    });

    counter = 0;
    let titles = $.map(data['data'], function (val, key) {
      counter+= 1;
      return {
        top: "" + (((counter-1)*100 + 5)/targets) + "%",
        text: key,
        left: 'center'
      }
    });

    counter = 0;
    let xAxis = $.map(data['data'], function (val, key) {
      counter +=1;
      return {
        id: counter-1,
        gridIndex: counter-1,
        type: 'time',
        min: attack_start,
        max: attack_end
      }
    });

    counter = 0;
    let yAxis = $.map(data['data'], function (val, key) {
      counter+=1;
      return {
        id: counter-1,
        gridIndex: counter-1,
        type: 'value',
        min: 0,
        max: data['max_attacks'] + data['max_attacks']*0.1
      }
    });


    counter=0;
    let series = $.map(data['data'], function (val, key) {
      counter+=1;
      return $.map(val, function (val, key) {
        return {
          name: key,
          data: $.map(val, function (a) {
              return [[Date.parse(a[0]), a[1]]];
            }),
          type: 'line',
          smooth: false,
          xAxisIndex: counter-1,
          yAxisIndex: counter-1
        }
      })
    })

    option = {
      legend: {},
      title: titles,
      backgroundColor: '#fff',
      xAxis: xAxis,
      yAxis: yAxis,
      grid: grids,
      series: series
    }


    if (option && typeof option === 'object') {
      myChart.setOption(option);
    }
    resizeEChart(canvas)
  })
}


function updateMultiSummary() {
  let ip = $("#global-filters-ip").val();

  if (ip === "") {
    alert("Please enter up in the global filters");
    return;
  }

  let canvas = $("#attack-multi-summary-canvas")
  let myChart = echarts.init(canvas.get(0));
  let option;

  canvas.show();
  myChart.showLoading();



  $.get("/multi_target_summary/" + ip, function (data) {
    myChart.hideLoading();

    canvas.height(800);

    if ('error' in data) {
      alert("Error: " + data['error']);
      return;
    }

    let series = $.map(data['data'], function(val, key) {
      return {
        name: key,
        data: val,
        type: 'scatter',
        symbolSize: function (data) {
            return data[2];
          }
      }
    })

    option = {
      backgroundColor: "#fff",
      legend: {},
      xAxis: {
        scale: true,
        type: 'category'
      },
      yAxis: {
        scale: true,
        type: 'category'
      },
      series: series
    }

    if (option && typeof option === 'object') {
      myChart.setOption(option);
    }
    resizeEChart(canvas)
  })
}

function updateTablesDaily() {
  let date = getDate("tables-daily-filters-date");
  if (date === "none") {
    alert("Моля изберете дата");
    return;
  }
  let result = $('#tables-daily-result')
  result.html('Loading ... ')
  $.get("/table_daily/" + date, function (data) {
    result.html(data)
  })
}

function updateTablesSimple() {
  let date = getDate("tables-simple-filters-date");
  if (date === "none") {
    alert("Моля въведете дата");
    return;
  }

  let result = $('#tables-simple-result')
  result.html('Loading ... ')
  $.get("/table_simple/" + date , function (data) {
    result.html(data)
  })
}

function updateTablesDetails() {
  let result = $('#tables-details-result')
  let start = getDate('tables-details-filters-startD')
  let end = getDate('tables-details-filters-endD')
  let res = $("#tables-details-filters-resolution").val();

  if (start === 'none') {
    alert('Моля изберета начална дата');
    return;
  }
  if (end === 'none') {
    alert('Моля изберете крайнта дата');
    return;
  }
  result.html('Loading ... ')
  $.get("/table_details/" + start + "/" + end + "/" + res, function (data) {
    result.html(data)
  })
}

function downloadCSV(csv, filename) {
    var csvFile;
    var downloadLink;

    // CSV file
    csvFile = new Blob([csv], {type: "text/csv"});

    // Download link
    downloadLink = document.createElement("a");

    // File name
    downloadLink.download = filename;

    // Create a link to the file
    downloadLink.href = window.URL.createObjectURL(csvFile);

    // Hide download link
    downloadLink.style.display = "none";

    // Add the link to DOM
    document.body.appendChild(downloadLink);

    // Click download link
    downloadLink.click();
}

function exportTableToCSV(table, filename) {
    var csv = [];
    var rows = document.querySelectorAll("#" + table + " tr");

    for (var i = 0; i < rows.length; i++) {
        var row = [], cols = rows[i].querySelectorAll("td, th");

        for (var j = 0; j < cols.length; j++)
            row.push(cols[j].innerText);

        csv.push(row.join(","));
    }

    // Download CSV file
    downloadCSV(csv.join("\n"), filename);
}

function exportTableToExcel(table, filename) {
        let uri = 'data:application/vnd.ms-excel;base64,',
        template = '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><title></title><head><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets><x:ExcelWorksheet><x:Name>{worksheet}</x:Name><x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet></x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--><meta http-equiv="content-type" content="text/plain; charset=UTF-8"/></head><body><table>{table}</table></body></html>',
        base64 = function(s) { return window.btoa(unescape(encodeURIComponent(s))) },         format = function(s, c) { return s.replace(/{(\w+)}/g, function(m, p) { return c[p]; })}

        if (!table.nodeType) table = document.getElementById(table)
        var ctx = {worksheet: 'main' || 'Worksheet', table: table.innerHTML}

        var link = document.createElement('a');
        link.download = filename;
        link.href = uri + base64(format(template, ctx));
        link.click();
}
