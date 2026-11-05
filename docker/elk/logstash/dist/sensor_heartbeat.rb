require "time"

def register(params)
  @sensor_name = /\A[A-Za-z0-9_.-]{1,512}\z/
  @version_tag = /\A[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}\z/
  @date = /\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})\z/
  @fields = %w[@timestamp type t-pot_sensor t-pot_hostname t-pot_ip_ext t-pot_ip_int heartbeat]
end

def reject_heartbeat(event, reason)
  logger.warn("Rejected sensor heartbeat", :sensor => event.get("t-pot_sensor"), :reason => reason)
  []
end

def filter(event)
  event.remove("[@metadata][tpot_heartbeat]")
  event.remove("[@metadata][tpot_heartbeat_history]")
  event.remove("t-pot_sensor")
  name = event.get("[headers][x_tpot_sensor]")
  event.remove("[headers][x_tpot_sensor]")
  if name.is_a?(String) && @sensor_name.match?(name)
    event.set("t-pot_sensor", name)
  end
  return [event] unless event.get("type") == "TPotSensorHeartbeat"
  return reject_heartbeat(event, "missing or invalid authenticated sensor name") unless event.get("t-pot_sensor")

  heartbeat = event.get("heartbeat")
  return reject_heartbeat(event, "heartbeat must be an object") unless heartbeat.is_a?(Hash)
  sequence = heartbeat["sequence"]
  unless sequence.is_a?(Integer) && sequence.between?(1, 9_223_372_036_854_775_807)
    return reject_heartbeat(event, "sequence must be a positive signed 64-bit integer")
  end
  version = heartbeat["version"]
  unless version.is_a?(String) && (version.empty? || @version_tag.match?(version))
    return reject_heartbeat(event, "version must be an image tag or empty for an older tpotinit")
  end
  started = heartbeat["started_at"]
  unless started.is_a?(String) && @date.match?(started)
    return reject_heartbeat(event, "started_at must be an ISO timestamp with timezone")
  end
  begin
    started = LogStash::Timestamp.new(Time.iso8601(started))
  rescue ArgumentError
    return reject_heartbeat(event, "started_at is not a valid date")
  end
  tags = event.get("tags")
  if tags.is_a?(Array) && tags.include?("_timestampparsefailure")
    return reject_heartbeat(event, "invalid sensor timestamp")
  end

  event.to_hash.keys.each { |key| event.remove(key) unless @fields.include?(key) }
  %w[t-pot_hostname t-pot_ip_ext t-pot_ip_int].each do |key|
    value = event.get(key)
    if !value.nil? && (!value.is_a?(String) || value.length > 253)
      logger.warn("Ignored malformed sensor heartbeat metadata", :sensor => name, :field => key)
      event.remove(key)
    end
  end
  event.set("heartbeat", {
    "version" => version, "sequence" => sequence, "started_at" => started,
    "received_at" => LogStash::Timestamp.now
  })
  event.set("[@metadata][tpot_heartbeat]", true)
  event.set("[@metadata][tpot_heartbeat_history]", sequence % 5 == 0)
  [event]
end
