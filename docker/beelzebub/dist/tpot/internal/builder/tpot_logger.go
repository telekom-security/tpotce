package builder

// T-Pot: flat JSON event log.
//
// T-Pot's Logstash pipeline, Kibana dashboards and ewsposter expect the flat
// log format of the former T-Pot fork of Beelzebub (src_ip, dest_port,
// username, input, session, ...). This trace strategy wraps the upstream
// strategy and additionally writes every event as one flat JSON line to the
// T-Pot log file. It is hooked in by tpot.patch in director.go.

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"sync"
	"time"

	"github.com/beelzebub-labs/beelzebub/v3/internal/parser"
	"github.com/beelzebub-labs/beelzebub/v3/internal/tracer"

	log "github.com/sirupsen/logrus"
)

const (
	tpotLogFileDefault = "./configurations/log/beelzebub.json"
	tpotLogFileEnv     = "BEELZEBUB_TPOT_LOG_FILE"
	// Sessions without an end event (e.g. dropped connections) are forgotten after this.
	tpotSessionTTL = 2 * time.Hour
	tpotSweepEvery = time.Minute
)

// Upstream event names mapped back to the names of the former T-Pot fork.
// Names not listed here (TELNET, MCP, TCP sessions) are passed through.
var tpotMessageNames = map[string]string{
	"New SSH Login Attempt":            "New SSH attempt",
	"New SSH Terminal Session":         "New SSH Session",
	"SSH Terminal Session Interaction": "New SSH Terminal Session",
	"SSH Raw Command":                  "New SSH Inline Session",
}

// Keys the former fork wrote even when empty, per (mapped) message.
var tpotAlwaysKeys = map[string][]string{
	"HTTP New request":  {"body", "request_cookies"},
	"HTTPS New Request": {"body", "request_cookies"},
	"New SSH Session":   {"input"},
}

type tpotSession struct {
	srcIP     string
	srcPort   string
	destPort  string
	start     time.Time
	lastEvent time.Time
}

type tpotLogger struct {
	mu        sync.Mutex
	out       *os.File
	destPorts map[string]string // service description -> port
	sessions  map[string]*tpotSession
	clients   map[string]tpotClient // remote address -> SSH client version
	lastSweep time.Time
	now       func() time.Time
}

type tpotClient struct {
	version string
	seen    time.Time
}

// tpotTraceStrategy returns a strategy that calls next and writes the flat T-Pot log line.
func tpotTraceStrategy(next tracer.Strategy, services []parser.BeelzebubServiceConfiguration) tracer.Strategy {
	path := os.Getenv(tpotLogFileEnv)
	if path == "" {
		path = tpotLogFileDefault
	}
	out, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0660)
	if err != nil {
		log.Fatalf("T-Pot: failed to open log file %s: %v", path, err)
	}
	t := newTPotLogger(out, services)
	return func(event tracer.Event) {
		next(event)
		t.write(event)
	}
}

func newTPotLogger(out *os.File, services []parser.BeelzebubServiceConfiguration) *tpotLogger {
	t := &tpotLogger{
		out:       out,
		destPorts: map[string]string{},
		sessions:  map[string]*tpotSession{},
		clients:   map[string]tpotClient{},
		now:       time.Now,
	}
	for _, service := range services {
		_, port, err := net.SplitHostPort(service.Address)
		if err != nil {
			continue
		}
		if existing, ok := t.destPorts[service.Description]; ok && existing != port {
			log.Warnf("T-Pot: services %q share the description, dest_port may be wrong", service.Description)
			continue
		}
		t.destPorts[service.Description] = port
	}
	return t
}

func (t *tpotLogger) write(event tracer.Event) {
	t.mu.Lock()
	defer t.mu.Unlock()

	for _, line := range t.lines(event) {
		data, err := json.Marshal(line)
		if err != nil {
			log.Errorf("T-Pot: failed to encode event: %v", err)
			continue
		}
		if _, err := t.out.Write(append(data, '\n')); err != nil {
			log.Errorf("T-Pot: failed to write event: %v", err)
		}
	}
}

// lines converts one upstream event into the T-Pot log line(s). Callers hold t.mu.
func (t *tpotLogger) lines(event tracer.Event) []map[string]any {
	now := t.now().UTC()
	t.expire(now)

	message := event.Msg
	if name, ok := tpotMessageNames[message]; ok {
		message = name
	}

	line := map[string]any{
		"level":     "info",
		"timestamp": now.Format(time.RFC3339Nano),
		"message":   message,
		"msg":       message,
	}
	set := func(key, value string) {
		if value != "" {
			line[key] = value
		}
	}

	destPort := t.destPorts[event.Description]
	srcIP, srcPort := event.SourceIp, event.SourcePort
	session := t.sessions[event.ID]

	switch event.Status {
	case tracer.Start.String():
		session = &tpotSession{srcIP: srcIP, srcPort: srcPort, destPort: destPort, start: now, lastEvent: now}
		t.sessions[event.ID] = session
		if client, ok := t.clients[event.RemoteAddr]; ok && event.Protocol == tracer.SSH.String() {
			set("client_version", client.version)
		}
	case tracer.Interaction.String():
		if session != nil {
			set("input_duration", fmt.Sprintf("%.2fs", now.Sub(session.lastEvent).Seconds()))
			session.lastEvent = now
		}
	case tracer.End.String():
		if session != nil {
			srcIP, srcPort, destPort = session.srcIP, session.srcPort, session.destPort
			set("session_duration", fmt.Sprintf("%.2fs", now.Sub(session.start).Seconds()))
			delete(t.sessions, event.ID)
		}
	}
	if event.Msg == "New SSH Login Attempt" && event.Client != "" {
		t.clients[event.RemoteAddr] = tpotClient{version: event.Client, seen: now}
	}

	set("src_ip", srcIP)
	set("src_port", srcPort)
	set("dest_port", destPort)
	set("status", event.Status)
	set("protocol", event.Protocol)
	set("session", event.ID)
	set("service", event.Description)
	set("environ", event.Environ)
	set("username", event.User)
	set("password", event.Password)
	set("client", event.Client)
	// The former fork logged TCP payloads as "command" and SSH input as "input".
	if event.Protocol == tracer.TCP.String() {
		set("command", event.Command)
		set("command_raw", event.CommandRaw)
	} else {
		set("input", event.Command)
		set("input_raw", event.CommandRaw)
	}
	set("output", event.CommandOutput)
	set("request_uri", event.RequestURI)
	set("request_method", event.HTTPMethod)
	set("body", event.Body)
	set("hostname", event.HostHTTPRequest)
	set("userAgent", event.UserAgent)
	set("request_cookies", event.Cookies)
	set("request_headers", event.Headers)
	set("tls_server_name", event.TLSServerName)
	set("handler", event.Handler)
	if len(event.Metadata) > 0 {
		line["metadata"] = event.Metadata
	}
	for _, key := range tpotAlwaysKeys[message] {
		if _, ok := line[key]; !ok {
			line[key] = ""
		}
	}

	lines := []map[string]any{line}

	// Upstream traces an inline SSH command as a single event and returns
	// without an end event, the former fork logged a start and an end line.
	if event.Msg == "SSH Raw Command" {
		delete(t.sessions, event.ID)
		end := map[string]any{
			"level":            "info",
			"timestamp":        line["timestamp"],
			"message":          "End SSH Inline Session",
			"msg":              "End SSH Inline Session",
			"status":           tracer.End.String(),
			"protocol":         event.Protocol,
			"session":          event.ID,
			"session_duration": "0.00s",
		}
		for _, key := range []string{"src_ip", "src_port", "dest_port"} {
			if value, ok := line[key]; ok {
				end[key] = value
			}
		}
		lines = append(lines, end)
	}
	return lines
}

// expire drops sessions and client versions that were never closed. Callers hold t.mu.
func (t *tpotLogger) expire(now time.Time) {
	if now.Sub(t.lastSweep) < tpotSweepEvery {
		return
	}
	t.lastSweep = now
	for id, session := range t.sessions {
		if now.Sub(session.lastEvent) > tpotSessionTTL {
			delete(t.sessions, id)
		}
	}
	for addr, client := range t.clients {
		if now.Sub(client.seen) > tpotSessionTTL {
			delete(t.clients, addr)
		}
	}
}
