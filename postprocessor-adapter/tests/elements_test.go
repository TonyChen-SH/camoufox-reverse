package elements

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/wspr-ncsu/visiblev8/post-processor/core"
)

func elementAggregator(t *testing.T, legacy bool) *CreateElementAggregator {
	t.Helper()
	metadata := "adapter-idldata.json"
	if legacy {
		metadata = filepath.Join(t.TempDir(), "legacy.json")
		data := `{"HTMLDocument":{"parent":null,"methods":["createElement"],"properties":[],"members":["createElement"]}}`
		if err := os.WriteFile(metadata, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("IDLDATA_FILE", metadata)
	agg, err := NewCreateElementAggregator()
	if err != nil {
		t.Fatal(err)
	}
	return agg.(*CreateElementAggregator)
}

func elementContext() *core.ExecutionContext {
	origin := &core.Origin{Origin: "https://example.test"}
	return &core.ExecutionContext{
		Origin: origin,
		Script: core.NewScriptInfo(core.NewIsolateInfo("0x1"), 1,
			`document.createElement("DIV");`, origin),
	}
}

func elementCounts(t *testing.T, agg *CreateElementAggregator) map[string]int {
	t.Helper()
	var stream bytes.Buffer
	ctx := &core.AggregationContext{Formats: core.FormatSet{"create_element": true}}
	if err := agg.DumpToStream(ctx, &stream); err != nil {
		t.Fatal(err)
	}
	counts := make(map[string]int)
	decoder := json.NewDecoder(&stream)
	for {
		var row []json.RawMessage
		if err := decoder.Decode(&row); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		if len(row) != 2 {
			t.Fatalf("unexpected output: %v", row)
		}
		var label string
		var payload struct {
			Tag    string `json:"tag_name"`
			Count  int    `json:"create_count"`
			Offset int    `json:"script_offset"`
		}
		if err := json.Unmarshal(row[0], &label); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(row[1], &payload); err != nil {
			t.Fatal(err)
		}
		if label != "create_element" {
			t.Fatalf("unexpected label: %s", label)
		}
		counts[fmt.Sprintf("%s@%d", payload.Tag, payload.Offset)] = payload.Count
	}
	return counts
}

func TestInheritedDocumentCreateElement(t *testing.T) {
	agg := elementAggregator(t, false)
	ctx := elementContext()
	for _, fields := range [][]string{
		{"0", "%createElement", "{1,HTMLDocument}", `"DIV"`},
		{"0", "%createElement", "{1,HTMLDocument}", `"div"`},
		{"31", "%createElement", "{2,Document}", `"SPAN"`},
		{"62", "%createElement", "{3,CustomDocument}", `"IGNORE"`},
	} {
		if err := agg.IngestRecord(ctx, 1, 'c', fields); err != nil {
			t.Fatal(err)
		}
	}
	want := map[string]int{"div@0": 2, "span@31": 1}
	if got := elementCounts(t, agg); !reflect.DeepEqual(got, want) {
		t.Fatalf("inherited Document tags/counts: got %v, want %v", got, want)
	}
}

func TestLegacyHTMLDocumentCreateElement(t *testing.T) {
	agg := elementAggregator(t, true)
	if err := agg.IngestRecord(elementContext(), 1, 'c', []string{
		"7", "%createElement", "{1,HTMLDocument}", `"SPAN"`,
	}); err != nil {
		t.Fatal(err)
	}
	if got := elementCounts(t, agg); !reflect.DeepEqual(got, map[string]int{"span@7": 1}) {
		t.Fatalf("legacy tag/count changed: %v", got)
	}
}

func TestMissingCreateElementArgument(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		t.Run(fmt.Sprintf("legacy=%t", legacy), func(t *testing.T) {
			defer func() {
				if value := recover(); value != nil {
					t.Fatalf("missing first argument panicked: %v", value)
				}
			}()
			agg := elementAggregator(t, legacy)
			if err := agg.IngestRecord(elementContext(), 1, 'c', []string{
				"0", "%createElement", "{1,HTMLDocument}",
			}); err != nil {
				t.Fatal(err)
			}
			if got := elementCounts(t, agg); len(got) != 0 {
				t.Fatalf("missing argument invented a tag: %v", got)
			}
		})
	}
}

func TestIncompleteNativeCallShape(t *testing.T) {
	for _, fields := range [][]string{{}, {"0"}, {"0", "%createElement"}} {
		t.Run(fmt.Sprintf("fields=%d", len(fields)), func(t *testing.T) {
			defer func() {
				if value := recover(); value != nil {
					t.Fatalf("incomplete record panicked: %v", value)
				}
			}()
			agg := elementAggregator(t, false)
			if err := agg.IngestRecord(elementContext(), 1, 'c', fields); err == nil {
				t.Fatal("incomplete native call shape was accepted")
			}
		})
	}
}
