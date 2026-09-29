package parser

// T-Pot: environment variables in service configurations.
//
// T-Pot passes the LLM settings from .env into the container, the service
// YAMLs reference them as ${NAME}. The expansion runs on the raw file, before
// both the unmarshal and the schema validation (which checks the raw
// document). Only the braced form is expanded, so a "$" in a regex such as
// "^ls$" is left alone. Hooked in by tpot.patch.

import (
	"os"
	"regexp"
)

var tpotEnvPattern = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`)

func tpotExpandEnv(buf []byte) []byte {
	return tpotEnvPattern.ReplaceAllFunc(buf, func(match []byte) []byte {
		return []byte(os.Getenv(string(match[2 : len(match)-1])))
	})
}
