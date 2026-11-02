package adblock

import (
	"bufio"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wspr-ncsu/visiblev8/post-processor/core"
)

type failingReader struct{}

func (failingReader) Read([]byte) (int, error) { return 0, errors.New("oracle-read") }

func TestRejectMissingResult(t *testing.T) {
	agg := &adblockAggregator{}
	if err := agg.readFromAdblock(bufio.NewScanner(strings.NewReader("")), 1); err == nil {
		t.Fatal("missing oracle result was accepted")
	}
}

func TestPropagateScannerError(t *testing.T) {
	agg := &adblockAggregator{}
	err := agg.readFromAdblock(bufio.NewScanner(failingReader{}), 1)
	if err == nil || !strings.Contains(err.Error(), "oracle-read") {
		t.Fatalf("scanner error was not propagated: %v", err)
	}
}

func TestRejectExtraResult(t *testing.T) {
	agg := &adblockAggregator{}
	row := "{\"url\":\"https://tracker.test/a.js\",\"origin\":\"example.test\",\"blocked\":true}\n"
	if err := agg.readFromAdblock(bufio.NewScanner(strings.NewReader(row+row)), 1); err == nil {
		t.Fatal("extra oracle result was accepted")
	}
}

func TestRejectMissingBlockedField(t *testing.T) {
	if _, err := NewScriptURLPair([]byte("{\"url\":\"https://tracker.test/a.js\",\"origin\":\"example.test\"}")); err == nil {
		t.Fatal("absent result was converted to blocked=false")
	}
}

func TestPropagateOracleExit(t *testing.T) {
	directory := t.TempDir()
	child := filepath.Join(directory, "oracle")
	script := "#!/bin/sh\nprintf '%s\\n' '{\"url\":\"https://tracker.test/a.js\",\"origin\":\"example.test\",\"blocked\":true}'\nexit 7\n"
	if err := os.WriteFile(child, []byte(script), 0700); err != nil { t.Fatal(err) }
	t.Setenv("ADBLOCK_BINARY", child)
	agg := &adblockAggregator{
		scriptList: map[int]*RawScript{1: {info: &core.ScriptInfo{
			URL: "https://tracker.test/a.js", FirstOrigin: &core.Origin{Origin: "https://example.test"}}}},
		adblockTmpFilename: filepath.Join(directory, "input.jsonl"),
	}
	err := agg.sendURLsToAdblock()
	if err == nil || !strings.Contains(err.Error(), "exit status 7") {
		t.Fatalf("nonzero oracle exit was not propagated: %v", err)
	}
}
