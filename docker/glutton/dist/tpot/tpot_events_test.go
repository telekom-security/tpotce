package producer

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/spf13/viper"
)

func TestTpotSessionLog(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sessions.jsonl")
	viper.Set("tpot.events", path)
	t.Cleanup(func() { viper.Set("tpot.events", nil) })
	p, err := New("sensor", "test")
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := p.logTpotEvent(&Event{Transport: "udp", SrcHost: "192.0.2.1",
				DstPort: 5060, Handler: "sip", Decoded: []string{"line\nbreak"}}); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 20 {
		t.Fatalf("want 20 complete events, got %d", len(lines))
	}
	for _, line := range lines {
		var event map[string]any
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatal(err)
		}
		if event["event_kind"] != "session" || event["transport"] != "udp" ||
			event["srcHost"] != "192.0.2.1" || event["handler"] != "sip" {
			t.Fatalf("unexpected session: %s", line)
		}
	}
}

type failingTpotWriter struct{}

func (failingTpotWriter) Write([]byte) (int, error) { return 0, os.ErrPermission }

func TestTpotSessionLogFailures(t *testing.T) {
	viper.Set("tpot.events", filepath.Join(t.TempDir(), "absent", "events"))
	t.Cleanup(func() { viper.Set("tpot.events", nil) })
	if _, err := New("sensor", "test"); err == nil {
		t.Fatal("unwritable session log must prevent startup")
	}
	p := &Producer{tpotEvents: failingTpotWriter{}}
	if err := p.logTpotEvent(&Event{}); !errors.Is(err, os.ErrPermission) {
		t.Fatalf("write error was hidden: %v", err)
	}
}
