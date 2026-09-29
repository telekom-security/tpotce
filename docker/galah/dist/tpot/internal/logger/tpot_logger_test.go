package logger

import (
	"bufio"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/0x4d31/galah/pkg/enrich"
	"github.com/0x4d31/galah/pkg/llm"
	"github.com/0x4d31/galah/pkg/suricata"
	cblog "github.com/charmbracelet/log"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func tpotTestLogger(t *testing.T) (*Logger, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "galah.json")
	l, err := New(path,
		llm.Config{Provider: "ollama", Model: "llama3.1", Temperature: 1},
		enrich.New(enrich.Config{CacheSize: 10, CacheTTL: time.Minute}),
		NewSessionizer(Config{CacheSize: 10, CacheTTL: time.Minute}),
		cblog.Default())
	require.NoError(t, err)
	t.Cleanup(func() { l.EventFile.Close() })
	return l, path
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

// The key sets are those the former T-Pot fork wrote, ewsposter and the Kibana dashboard rely on them.
func TestTPotSuccessfulResponseMatchesForkFormat(t *testing.T) {
	l, path := tpotTestLogger(t)

	r := httptest.NewRequest("POST", "/wp-login.php?x=1", strings.NewReader("log=admin"))
	r.RemoteAddr = "127.0.0.1:40000"
	r.Header.Set("User-Agent", "tpot-test")
	l.LogEvent(r, llm.JSONResponse{
		Headers: map[string]string{"Content-Type": "text/html", "Server": "nginx"},
		Body:    "<html></html>",
	}, "8080", "static", nil)

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 1)
	line := lines[0]

	assert.Equal(t, []string{
		"dest_port", "hostname", "level", "msg",
		"request.body", "request.bodySha256", "request.headers.User-Agent",
		"request.headers.sorted", "request.headers.sortedSha256", "request.method",
		"request.protocol", "request.requestURI", "request.userAgent",
		"response.body", "response.headers.Content-Type", "response.headers.Server",
		"response.metadata.generationSource", "response.metadata.model",
		"response.metadata.provider", "response.metadata.temperature",
		"sensorName", "session", "src_ip", "src_port", "timestamp",
	}, tpotKeys(line))
	assert.Equal(t, "successfulResponse", line["msg"])
	assert.Equal(t, "info", line["level"])
	assert.Equal(t, "8080", line["dest_port"])
	assert.Equal(t, "127.0.0.1", line["src_ip"])
	assert.Equal(t, "40000", line["src_port"])
	assert.Equal(t, "POST", line["request.method"])
	assert.Equal(t, "/wp-login.php?x=1", line["request.requestURI"])
	assert.Equal(t, "tpot-test", line["request.headers.User-Agent"])
	assert.Equal(t, "log=admin", line["request.body"])
	assert.Equal(t, "static", line["response.metadata.generationSource"])
	// Unlike upstream, the LLM details are logged for static and cached responses as well
	assert.Equal(t, "ollama", line["response.metadata.provider"])
	assert.Equal(t, "llama3.1", line["response.metadata.model"])
	assert.NotEmpty(t, line["session"])

	_, err := time.Parse(time.RFC3339Nano, line["timestamp"].(string))
	assert.NoError(t, err)
}

func TestTPotFailedResponseKeepsLogstashType(t *testing.T) {
	l, path := tpotTestLogger(t)

	r := httptest.NewRequest("GET", "/admin", nil)
	r.RemoteAddr = "127.0.0.1:40001"
	l.LogError(r, "not json", "80", errors.New("invalidJSONResponse: unexpected end of JSON input"))

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 1)
	line := lines[0]

	assert.Equal(t, "failedResponse: returned 500 internal server error", line["msg"])
	assert.Equal(t, "error", line["level"])
	assert.NotContains(t, line, "type", "a top-level type replaces the Logstash type Galah")
	assert.Equal(t, "invalidJSONResponse", line["error_type"])
	assert.Equal(t, "unexpected end of JSON input", line["fields.msg"])
	assert.Equal(t, "not json", line["invalidResponse"])
	assert.Equal(t, "llama3.1", line["response.metadata.model"])
	assert.NotContains(t, line, "response.metadata.generationSource")
	assert.NotContains(t, line, "response.body")
	assert.Equal(t, "80", line["dest_port"])
}

func TestTPotSuricataMatches(t *testing.T) {
	l, path := tpotTestLogger(t)

	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "127.0.0.1:40002"
	l.LogEvent(r, llm.JSONResponse{Headers: map[string]string{}, Body: ""}, "80", "llm",
		[]suricata.Rule{{SID: "2000001", Msg: "ET TEST"}})

	lines := tpotReadLines(t, path)
	require.Len(t, lines, 1)
	assert.Equal(t, []any{map[string]any{"sid": "2000001", "msg": "ET TEST"}}, lines[0]["suricata_matches"])
	assert.NotContains(t, lines[0], "tags")
}
