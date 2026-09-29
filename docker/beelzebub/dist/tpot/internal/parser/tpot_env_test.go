package parser

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestTPotExpandEnv(t *testing.T) {
	t.Setenv("LLM_PROVIDER", "ollama")
	t.Setenv("LLM_MODEL", "openchat")

	in := "regex: \"^ls$\"\nprovider: \"${LLM_PROVIDER}\"\nmodel: \"${LLM_MODEL}\"\nhost: \"${TPOT_TEST_UNSET}\"\nkeep: \"$LLM_MODEL\"\n"
	want := "regex: \"^ls$\"\nprovider: \"ollama\"\nmodel: \"openchat\"\nhost: \"\"\nkeep: \"$LLM_MODEL\"\n"
	assert.Equal(t, want, string(tpotExpandEnv([]byte(in))))
}

func TestTPotExpandEnvBeforeValidation(t *testing.T) {
	t.Setenv("LLM_PROVIDER", "ollama")
	t.Setenv("LLM_MODEL", "openchat")

	files := map[string]string{
		"ssh-22.yaml": `apiVersion: "v1"
protocol: "ssh"
address: ":22"
description: "SSH interactive LLM"
commands:
  - regex: "^(.+)$"
    plugin: "LLMHoneypot"
serverVersion: "OpenSSH_7.9p1"
serverName: "ubuntu"
passwordRegex: ".*"
deadlineTimeoutSeconds: 6000
plugin:
  llmProvider: "${LLM_PROVIDER}"
  llmModel: "${LLM_MODEL}"
  host: "${LLM_HOST}"
`,
	}
	p := Init("", "services")
	p.gelAllFilesNameByDirNameDependency = func(string) ([]string, error) { return []string{"ssh-22.yaml"}, nil }
	p.readFileBytesByFilePathDependency = func(path string) ([]byte, error) { return []byte(files["ssh-22.yaml"]), nil }

	services, issues, err := p.ReadConfigurationsServicesForValidation()
	require.NoError(t, err)
	require.Empty(t, issues)
	require.Len(t, services, 1)
	assert.Equal(t, "ollama", services[0].Plugin.LLMProvider)
	assert.Equal(t, "openchat", services[0].Plugin.LLMModel)
	assert.Equal(t, "", services[0].Plugin.Host)

	result := Validate(services, nil)
	assert.Zero(t, result.TotalErrors)
}
