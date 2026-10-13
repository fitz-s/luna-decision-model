package run
import (
 "encoding/json"
 "testing"
)
func TestClaudeHookContextIsInsideHookSpecificOutput(t *testing.T) {
 value:=hookOutput{HookSpecificOutput:&hookSpecific{HookEventName:"PreToolUse",AdditionalContext:"context",UpdatedInput:map[string]any{"file_path":"fixture","offset":1,"limit":10,"pages":"2"}}}
 raw,err:=json.Marshal(value);if err!=nil {t.Fatal(err)}
 var output map[string]any;if err=json.Unmarshal(raw,&output);err!=nil {t.Fatal(err)}
 if _,ok:=output["additionalContext"];ok {t.Fatal("context was placed outside the Claude hook envelope")}
 nested:=output["hookSpecificOutput"].(map[string]any)
 if nested["additionalContext"]!="context" || nested["hookEventName"]!="PreToolUse" {t.Fatal("required Claude hook fields missing")}
 if nested["updatedInput"].(map[string]any)["pages"]!="2" {t.Fatal("extra caller input was lost")}
}
