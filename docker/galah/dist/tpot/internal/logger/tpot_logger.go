package logger

// T-Pot: flat JSON event log.
//
// T-Pot's Logstash pipeline, Kibana dashboards and ewsposter expect the flat
// log format of the former T-Pot fork of Galah (src_ip, dest_port, session,
// request.*, response.*). tpotWrite replaces the upstream event logger calls
// in LogEvent and LogError (hooked in by tpot.patch) and writes that format.

import (
	"encoding/json"
	"strings"
	"sync"
	"time"

	"github.com/0x4d31/galah/pkg/llm"
)

var tpotWriteMu sync.Mutex

// tpotWrite writes one event, built from the upstream fields, as a flat JSON line.
func (l *Logger) tpotWrite(level, msg string, fields map[string]any) {
	line := tpotFlatten(level, msg, fields, l.LLMConfig)

	data, err := json.Marshal(line)
	if err != nil {
		l.Logger.WithPrefix("GALAH").Errorf("T-Pot: failed to encode event: %s", err)
		return
	}

	tpotWriteMu.Lock()
	defer tpotWriteMu.Unlock()
	if _, err := l.EventFile.Write(append(data, '\n')); err != nil {
		l.Logger.WithPrefix("GALAH").Errorf("T-Pot: failed to write event: %s", err)
	}
}

func tpotFlatten(level, msg string, fields map[string]any, llmConfig llm.Config) map[string]any {
	line := map[string]any{
		"level": level,
		"msg":   msg,
	}

	timestamp := time.Now()
	if eventTime, ok := fields["eventTime"].(time.Time); ok {
		timestamp = eventTime
	}
	line["timestamp"] = timestamp.UTC().Format(time.RFC3339Nano)

	for from, to := range map[string]string{
		"srcIP":      "src_ip",
		"srcHost":    "hostname",
		"srcPort":    "src_port",
		"port":       "dest_port",
		"sensorName": "sensorName",
	} {
		if value, ok := fields[from]; ok {
			line[to] = value
		}
	}
	if tags, ok := fields["tags"].([]string); ok && len(tags) > 0 {
		line["tags"] = strings.Join(tags, ",")
	}

	if request, ok := fields["httpRequest"].(HTTPRequest); ok {
		line["session"] = request.SessionID
		line["request.method"] = request.Method
		line["request.protocol"] = request.ProtocolVersion
		line["request.requestURI"] = request.Request
		line["request.userAgent"] = request.UserAgent
		line["request.body"] = request.Body
		line["request.bodySha256"] = request.BodySha256
		line["request.headers.sorted"] = request.HeadersSorted
		line["request.headers.sortedSha256"] = request.HeadersSortedSha256
		for name, value := range request.Headers {
			line["request.headers."+name] = value
		}
	}

	if response, ok := fields["httpResponse"].(llm.JSONResponse); ok {
		for name, value := range response.Headers {
			line["response.headers."+name] = value
		}
		line["response.body"] = response.Body
	}

	// The former fork logged the LLM details with every event, not only for LLM responses
	if metadata, ok := fields["responseMetadata"].(ResponseMetadata); ok && msg == "successfulResponse" {
		line["response.metadata.generationSource"] = metadata.GenerationSource
	}
	line["response.metadata.provider"] = llmConfig.Provider
	line["response.metadata.model"] = llmConfig.Model
	line["response.metadata.temperature"] = llmConfig.Temperature

	// The former fork put the error type on the top level as "type", which
	// replaced the Logstash type of the event, so it is logged as error_type
	if errorInfo, ok := fields["error"].(map[string]any); ok {
		if value, ok := errorInfo["type"]; ok {
			line["error_type"] = value
		}
		if value, ok := errorInfo["msg"]; ok {
			line["fields.msg"] = value
		}
		if value, ok := errorInfo["invalidResponse"]; ok {
			line["invalidResponse"] = value
		}
	}

	if matches, ok := fields["suricataMatches"]; ok {
		line["suricata_matches"] = matches
	}

	return line
}
