// ┌┬┐┬┌┬┐┌─┐
//  │ ││││├┤
//  ┴ ┴┴ ┴└─┘

window.onload = displayClock();
// Clock function
function displayClock() {
  const monthNames = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];

  // Get clock elements
  var d = new Date();
  var mm = monthNames[d.getMonth()];
  var dd = d.getDate();
  var min = (mins = ('0' + d.getMinutes()).slice(-2));
  var hh = d.getHours();
  var ampm = '';

  // Display clock elements
  document.getElementById('hour').innerText = hh;
  document.getElementById('separator').innerHTML = ' : ';
  document.getElementById('minutes').innerText = min + ampm;
  setTimeout(displayClock, 1000);
}
