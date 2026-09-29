package builder

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
	"time"

	"github.com/beelzebub-labs/beelzebub/v3/internal/parser"
	"github.com/beelzebub-labs/beelzebub/v3/internal/tracer"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func tpotTestLogger(t *testing.T) (*tpotLogger, string, *time.Time) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "beelzebub.json")
	out, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0660)
	require.NoError(t, err)
	t.Cleanup(func() { out.Close() })

	logger := newTPotLogger(out, []parser.BeelzebubServiceConfiguration{
		{Address: ":22", Description: "SSH interactive LLM"},
		{Address: ":8080", Description: "Apache 401"},
		{Address: ":3306", Description: "Mysql 8.0.29"},
	})
	clock := time.Date(2026, 9, 29, 12, 0, 0, 0, time.UTC)
	logger.now = func() time.Time { return clock }
	return logger, path, &clock
}

func tpotReadLines(t *testing.T, path string) []map[string]any {
	t.Helper()
	file, err := os.Open(path)
	require.NoError(t, err)
	defer file.Close()

	var lines []map[string]any
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var line map[string]any
		require.NoError(t, json.Unmarshal(scanner.Bytes(), &line))
		lines = append(lines, line)
	}
	require.NoError(t, scanner.Err())
	return lines
}

func tpotKeys(line map[string]any) []string {
	keys := make([]string, 0, len(line))
	for key := range line {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// The key sets per message are those the former T-Pot fork wrote, plus level/msg/timestamp.
func TestTPotSSHSessionMatchesForkFormat(t *testing.T) {
	logger, path, clock := tpotTestLogger(t)

	logger.write(tracer.Event{
		Msg: "New SSH Login Attempt", Protocol: "SSH", Status: tracer.Stateless.String(),
		User: "root", Password: "123456", Client: "SSH-2.0-OpenSSH_9.6",
		RemoteAddr: "192.0.2.1:40000", SourceIp: "192.0.2.1", SourcePort: "40000",
		ID: "attempt-1", Description: "SSH interactive LLM",
	})
	logger.write(tracer.Event{
		Msg: "New SSH Terminal Session", Protocol: "SSH", Status: tracer.Start.String(),
		RemoteAddr: "192.0.2.1:40000", SourceIp: "192.0.2.1", SourcePort: "40000",
		ID: "session-1", Environ: "LANG=C", User: "root", Description: "SSH interactive LLM",
	})
	*clock = clock.Add(1500 * time.Millisecond)
	logger.write(tracer.Event{
		Msg: "SSH Terminal Session Interaction", Protocol: "SSH", Status: tracer.Interaction.String(),
		RemoteAddr: "192.0.2.1:40000", SourceIp: "192.0.2.1", SourcePort: "40000",
		Command: "uname -a", CommandOutput: "Linux ubuntu", ID: "session-1",
		Description: "SSH interactive LLM", Handler: "llm",
	})
	*clock = clock.Add(2 * time.Second)
	logger.write(tracer.Event{Msg: "End SSH Session", Protocol: "SSH", Status: tracer.End.String(), ID: "session-1"})

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 4)

	assert.Equal(t, "New SSH attempt", lines[0]["message"])
	assert.Equal(t, []string{"client", "dest_port", "level", "message", "msg", "password", "protocol",
		"service", "session", "src_ip", "src_port", "status", "timestamp", "username"}, tpotKeys(lines[0]))
	assert.Equal(t, "22", lines[0]["dest_port"])

	assert.Equal(t, "New SSH Session", lines[1]["message"])
	assert.Equal(t, "SSH-2.0-OpenSSH_9.6", lines[1]["client_version"])
	assert.Equal(t, []string{"client_version", "dest_port", "environ", "input", "level", "message", "msg", "protocol",
		"service", "session", "src_ip", "src_port", "status", "timestamp", "username"}, tpotKeys(lines[1]))
	assert.Equal(t, "", lines[1]["input"])

	assert.Equal(t, "New SSH Terminal Session", lines[2]["message"])
	assert.Equal(t, "uname -a", lines[2]["input"])
	assert.Equal(t, "Linux ubuntu", lines[2]["output"])
	assert.Equal(t, "1.50s", lines[2]["input_duration"])

	assert.Equal(t, "End SSH Session", lines[3]["message"])
	assert.Equal(t, []string{"dest_port", "level", "message", "msg", "protocol", "session",
		"session_duration", "src_ip", "src_port", "status", "timestamp"}, tpotKeys(lines[3]))
	assert.Equal(t, "3.50s", lines[3]["session_duration"])
	assert.Equal(t, "192.0.2.1", lines[3]["src_ip"])
	assert.Equal(t, "22", lines[3]["dest_port"])
	assert.Empty(t, logger.sessions)

	_, err := time.Parse(time.RFC3339Nano, lines[0]["timestamp"].(string))
	assert.NoError(t, err)
}

func TestTPotSSHInlineSessionAddsEndLine(t *testing.T) {
	logger, path, _ := tpotTestLogger(t)

	logger.write(tracer.Event{
		Msg: "SSH Raw Command", Protocol: "SSH", Status: tracer.Start.String(),
		RemoteAddr: "192.0.2.2:40001", SourceIp: "192.0.2.2", SourcePort: "40001",
		ID: "inline-1", User: "root", Description: "SSH interactive LLM",
		Command: "id", CommandOutput: "uid=0(root)",
	})

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 2)
	assert.Equal(t, "New SSH Inline Session", lines[0]["message"])
	assert.Equal(t, "id", lines[0]["input"])
	assert.Equal(t, "End SSH Inline Session", lines[1]["message"])
	assert.Equal(t, "inline-1", lines[1]["session"])
	assert.Equal(t, "22", lines[1]["dest_port"])
	assert.Empty(t, logger.sessions)
}

func TestTPotHTTPAndTCPFields(t *testing.T) {
	logger, path, _ := tpotTestLogger(t)

	logger.write(tracer.Event{
		Msg: "HTTP New request", Protocol: "HTTP", Status: tracer.Stateless.String(),
		RequestURI: "/wp-login.php", HTTPMethod: "POST", Body: "log=admin", HostHTTPRequest: "example.org",
		UserAgent: "curl/8", Cookies: "a=b", Headers: "[Key: Accept, values: */*],",
		HeadersMap: map[string][]string{"Accept": {"*/*"}},
		RemoteAddr: "192.0.2.3:40002", SourceIp: "192.0.2.3", SourcePort: "40002",
		ID: "http-1", Description: "Apache 401",
	})
	logger.write(tracer.Event{
		Msg: "New TCP attempt", Protocol: "TCP", Status: tracer.Stateless.String(),
		Command: "\x03select", CommandRaw: `\x03select`,
		RemoteAddr: "192.0.2.4:40003", SourceIp: "192.0.2.4", SourcePort: "40003",
		ID: "tcp-1", Description: "Mysql 8.0.29",
	})

	logger.write(tracer.Event{
		Msg: "HTTP New request", Protocol: "HTTP", Status: tracer.Stateless.String(), RequestURI: "/",
		RemoteAddr: "192.0.2.3:40004", SourceIp: "192.0.2.3", SourcePort: "40004", ID: "http-2", Description: "Apache 401",
	})

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 3)
	assert.Equal(t, "", lines[2]["body"])
	assert.Equal(t, "", lines[2]["request_cookies"])
	assert.Equal(t, []string{"body", "dest_port", "hostname", "level", "message", "msg", "protocol",
		"request_cookies", "request_headers", "request_method", "request_uri", "service", "session",
		"src_ip", "src_port", "status", "timestamp", "userAgent"}, tpotKeys(lines[0]))
	assert.Equal(t, "8080", lines[0]["dest_port"])

	assert.Equal(t, "New TCP attempt", lines[1]["message"])
	assert.Equal(t, "\x03select", lines[1]["command"])
	assert.Equal(t, `\x03select`, lines[1]["command_raw"])
	assert.Equal(t, "3306", lines[1]["dest_port"])
	assert.NotContains(t, lines[1], "input")
}

func TestTPotExpiresStaleSessions(t *testing.T) {
	logger, _, clock := tpotTestLogger(t)

	logger.write(tracer.Event{Msg: "New TCP Session", Protocol: "TCP", Status: tracer.Start.String(),
		RemoteAddr: "192.0.2.5:1", SourceIp: "192.0.2.5", SourcePort: "1", ID: "tcp-2", Description: "Mysql 8.0.29"})
	require.Len(t, logger.sessions, 1)

	*clock = clock.Add(tpotSessionTTL + tpotSweepEvery)
	logger.write(tracer.Event{Msg: "New TCP attempt", Protocol: "TCP", Status: tracer.Stateless.String(), ID: "tcp-3"})
	assert.Empty(t, logger.sessions)
}

// T-Pot derives dest_port from the service description, so the shipped descriptions must be unique.
func TestTPotShippedServiceDescriptionsAreUnique(t *testing.T) {
	dir := os.Getenv("TPOT_SERVICES_DIR")
	if dir == "" {
		t.Skip("TPOT_SERVICES_DIR not set")
	}
	services, err := parser.Init("", dir).ReadConfigurationsServices()
	require.NoError(t, err)
	require.NotEmpty(t, services)

	seen := map[string]string{}
	for _, service := range services {
		if other, ok := seen[service.Description]; ok {
			t.Errorf("%s and %s share the description %q", other, service.Filename, service.Description)
		}
		seen[service.Description] = service.Filename
	}
}
