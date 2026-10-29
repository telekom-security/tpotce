package producer

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/spf13/viper"
	lumberjack "gopkg.in/natefinch/lumberjack.v2"
)

func (p *Producer) initTpotEvents() error {
	path := viper.GetString("tpot.events")
	if path == "" {
		return nil
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return fmt.Errorf("open T-Pot session log: %w", err)
	}
	if err := file.Close(); err != nil {
		return err
	}
	p.tpotEvents = &lumberjack.Logger{Filename: path, MaxSize: 200, MaxAge: 356, Compress: true}
	return nil
}

func (p *Producer) logTpotEvent(event *Event) error {
	if p.tpotEvents == nil {
		return nil
	}
	record := struct {
		Kind string `json:"event_kind"`
		*Event
	}{Kind: "session", Event: event}
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	_, err = p.tpotEvents.Write(append(data, '\n'))
	return err
}
