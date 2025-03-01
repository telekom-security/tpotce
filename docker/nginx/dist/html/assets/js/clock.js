function updateClock() {
    const now = new Date();
    const hours = ('0' + now.getHours()).slice(-2);
    const minutes = ('0' + now.getMinutes()).slice(-2);
    const day = ('0' + now.getDate()).slice(-2);
    const month = ('0' + (now.getMonth() + 1)).slice(-2);
    const year = now.getFullYear();
    document.getElementById('clock').textContent = hours + ':' + minutes + ' | ' + day + '/' + month + '/' + year;
    setTimeout(updateClock, 1000);
}
updateClock();
