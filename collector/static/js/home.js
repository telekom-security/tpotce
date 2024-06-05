// Production functions

    function loadIps(callback, days=0, limit=0)
    {
        params = '?days=' + parseInt(days, 10) + '&limit=' + parseInt(limit, 10)
        params = params + '&show_iso'
        $.ajax({
            type: "GET",
            url: "/API/report/ips" + params,
            // You are expected to receive the generated JSON (json_encode($data))
            dataType: "json",
            success: callback
        });
    }
    function loadCountries(callback, days=0, limit=0)
    {
        params = '?days=' + parseInt(days, 10) + '&limit=' + parseInt(limit, 10)

        $.ajax({
            type: "GET",
            url: "/API/report/countries" + params,
            // You are expected to receive the generated JSON (json_encode($data))
            dataType: "json",
            success: callback
        });
    }

    function loadProtocols(callback, days=1, limit=10)
    {
        params = '?days=' + parseInt(days, 10) + '&limit=' + parseInt(limit, 10)

        $.ajax({
            type: "GET",
            url: "/API/report/protocols" + params,
            // You are expected to receive the generated JSON (json_encode($data))
            dataType: "json",
            success: callback
        });
    }

    function add_rows(targetId, data)
    {
        var table = document.getElementById(targetId).getElementsByTagName('tbody')[0];
        data.forEach((data_row) => {
            var row = table.insertRow();
            var cell = Array();
            data_row.forEach((data_cell, index) => {
                cell[index] = row.insertCell(index)
                cell[index].innerHTML = data_cell;
            });
        });
        $('.' + targetId + '-spinner').hide();
    }

    function parseCountriesForIps(response)
    {
        let data = Array();
        response.forEach((row) => {
            let entry = Array();
            entry.push(row[0]);
            let image = 'N/A'
            if (row[1] !== null)
            {
                image = '<img src="/static/images/iso-flag-png/' + row[1].toLowerCase() + '.png" class="flag" alt="iso-flag"/>'
            }
            entry.push(image);
            entry.push(row[2]);
            data.push(entry);
        });
        return data;
    }

    function parseCountries(response)
    {
        let data = Array();
        response.forEach((row) => {
            let entry = Array();
            let image = '<img src="/static/images/iso-flag-png/' + row[0].toLowerCase() + '.png" class="flag" alt="iso-flag"/>'
            entry.push(image);
            entry.push(row[1]);
            data.push(entry);
        });
        return data;
    }
     function loadIpTop1024HIpReport(response)
    {
        let data = parseCountriesForIps(response);
        add_rows("table-24h", data);
        loadIps(loadIpTop107DIpReport, 7, 10);
    }
    function loadIpTop107DIpReport(response)
    {
        let data = parseCountriesForIps(response)
        add_rows("table-7D", data);
        loadIps(loadIpTop1030DIpReport, 30, 10);
    }
    function loadIpTop1030DIpReport(response)
    {
        let data = parseCountriesForIps(response);
        add_rows("table-30D", data);

    }

    function loadProtocols24HReport(response)
    {
        add_rows("table-proto-24h", response);
    }
    function loadCountriesTop101DReport(response)
    {
        data = parseCountries(response);
        add_rows("table-countries-24h", data);
        loadCountries(loadCountriesTop107DReport, 7, 10);
    }
    function loadCountriesTop107DReport(response)
    {
        data = parseCountries(response);
        add_rows("table-countries-7D", data);
        loadCountries(loadCountriesTop1030DReport, 30, 10);
    }

    function loadCountriesTop1030DReport(response)
    {
        data = parseCountries(response);
        add_rows("table-countries-30D", data);
    }

    function loadTypePerServer24HReport(response)
    {
        parseTypePerServer("table-type-per-server-24H", response);
        loadTypePerServer(loadTypePerServer24HBGReport, 1, 'bg');
    }
     function loadTypePerServer24HBGReport(response)
    {
        parseTypePerServer("table-type-per-server-24H-bg", response);
        loadTypePerServer(loadTypePerServer7DReport, 7);
    }
    function loadTypePerServer7DReport(response)
    {
        parseTypePerServer("table-type-per-server-7D", response);
        loadTypePerServer(loadTypePerServer7DBGReport, 7, 'bg');
    }
    function loadTypePerServer7DBGReport(response)
    {
        parseTypePerServer("table-type-per-server-7D-bg", response);
        loadTypePerServer(loadTypePerServer30DReport, 30);
    }
    function loadTypePerServer30DReport(response)
    {
        parseTypePerServer("table-type-per-server-30D", response);
        loadTypePerServer(loadTypePerServer30DBGReport, 30, 'bg');
    }
    function loadTypePerServer30DBGReport(response)
    {
        parseTypePerServer("table-type-per-server-30D-bg", response);
    }
    // Load on document ready
    $(document).ready(function(){
        loadIps(loadIpTop1024HIpReport, 1, 10);
        loadProtocols(loadProtocols24HReport);
        loadCountries(loadCountriesTop101DReport, 1, 10);
        loadTypePerServer(loadTypePerServer24HReport, 1);
    });


    // test

    function loadTypePerServer(callback, days=1, iso='any')
    {
        params = '?days=' + parseInt(days, 10) + '&iso=' + iso

        $.ajax({
            type: "GET",
            url: "/API/report/type_per_server" + params,
            // You are expected to receive the generated JSON (json_encode($data))
            dataType: "json",
            success: callback
        });
    }
    function parseTypePerServer(targetID, response)
    {
        response = JSON.parse(response)
        let storage = Array();
        let table = document.getElementById(targetID)
        let header = table.createTHead();
        let tbody = table.getElementsByTagName('tbody')[0];
        var title = header.insertRow(1);
        var cell = title.insertCell(0);
        cell.innerHTML = "Сървър";

        var index = 1;
        // we are going to build javascript dict ... and then double down and fill the table
        for (const [key, value] of Object.entries(response)) {
            var i = 1;
            var row = tbody.insertRow();
            var cell = row.insertCell(0);
            cell.innerHTML = value['name'] + ' (' + value['ip'] + ')'
            for (const [key1, value1] of Object.entries(value['data']))
            {
                if (key1 == 'name')
                {
                    continue;
                }
                // We don't have this ... so we add it
                if (!storage.hasOwnProperty(key1))
                {
                    storage[key1] = index;
                    var title = table.rows[1];
                    var cell = title.insertCell(index);
                    if (key1 == '01_comult')
                    {
                        cell.innerHTML = '<b> Общо </b>';
                    }
                    else
                    {
                        cell.innerHTML = key1;
                    }

                    index ++;
                }
                var cell = row.insertCell(i++);
                if (key1 == '01_comult')
                {
                     cell.innerHTML = '<b>' + value1 + '</b>';
                }
                else
                {
                    cell.innerHTML = value1
                }

            }
        }
        $('.' + targetID + '-spinner').hide();
    }
