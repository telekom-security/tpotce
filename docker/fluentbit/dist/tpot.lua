-- The Lua filters of fluent-bit.yaml. The fields of the sensor come from elk_environment of tpotinit
-- (entrypoint.sh); one without a value stays out (an empty t-pot_ip_ext is no address).

local stamp = {
  ["t-pot_hostname"] = os.getenv("MY_HOSTNAME"),
  ["t-pot_ip_ext"] = os.getenv("MY_EXTIP"),
  ["t-pot_ip_int"] = os.getenv("MY_INTIP"),
}

local function add_stamp(record)
  for key, value in pairs(stamp) do
    if value ~= nil and value ~= "" then
      record[key] = value
    end
  end
end

-- A log line: its type is the tag of its input without "tpot."
function tag_record(tag, ts, record)
  record["type"] = string.sub(tag, 6)
  add_stamp(record)
  return 2, ts, record
end

-- The heartbeat: a whole Lua number is packed as an integer, so sequence goes as 1, 2, ...
local sequence = 0

function heartbeat(tag, ts, record)
  sequence = sequence + 1
  add_stamp(record)
  record["heartbeat"] = {
    sequence = sequence,
    started_at = os.getenv("TPOT_FLUENTBIT_START") or "",
    version = os.getenv("MY_VERSION") or "",
  }
  return 2, ts, record
end
