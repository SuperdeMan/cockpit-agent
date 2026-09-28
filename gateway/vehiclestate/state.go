// Package vehiclestate validates the same wire contract as runtime.vehicle_state.
// It owns no command authority and does not infer a vehicle from request payloads.
package vehiclestate

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"regexp"
	"sort"
	"strconv"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/cockpit/car-agent/gateway/deployprofile"
)

const MaxPayload = 128 * 1024
const MaxSignals = 256
const ClockSkewMS int64 = 5000
const LegacyVehicle = "v1"
const domain = "cockpit.vehicle-state.v2\x00"

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_.:/-]{1,128}$`)
var keyPattern = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.:-]{0,127}$`)
var integerPattern = regexp.MustCompile(`^-?[0-9]+$`)
var hashPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

func fail(code string) error { return errors.New(code) }

func object(value any) (map[string]any, error) {
	x, ok := value.(map[string]any)
	if !ok {
		return nil, fail("invalid_object")
	}
	return x, nil
}

// Walk tokens before decoding so duplicate keys, invalid UTF-8 and trailing JSON
// cannot have different meanings in the Python and Go implementations.
func decode(raw []byte) (map[string]any, error) {
	if !utf8.Valid(raw) {
		return nil, fail("invalid_json")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var walk func(int) error
	walk = func(depth int) error {
		if depth > 32 {
			return fail("invalid_json")
		}
		t, err := d.Token()
		if err != nil {
			return err
		}
		if delim, ok := t.(json.Delim); ok {
			switch delim {
			case '{':
				seen := map[string]bool{}
				for d.More() {
					key, err := d.Token()
					if err != nil {
						return err
					}
					s, ok := key.(string)
					if !ok || seen[s] {
						return fail("invalid_json")
					}
					seen[s] = true
					if err := walk(depth + 1); err != nil {
						return err
					}
				}
			case '[':
				for d.More() {
					if err := walk(depth + 1); err != nil {
						return err
					}
				}
			default:
				return fail("invalid_json")
			}
			_, err = d.Token()
			return err
		}
		return nil
	}
	if err := walk(0); err != nil {
		return nil, fail("invalid_json")
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, fail("invalid_json")
	}
	d = json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var value any
	if d.Decode(&value) != nil {
		return nil, fail("invalid_json")
	}
	return object(value)
}

func text(value any) (string, bool) { s, ok := value.(string); return s, ok }
func identifier(value any) (string, error) {
	s, ok := text(value)
	if !ok || !idPattern.MatchString(s) {
		return "", fail("invalid_identity")
	}
	return s, nil
}
func integer(value any, minimum, maximum int64) (int64, error) {
	n, ok := value.(json.Number)
	if !ok || !integerPattern.MatchString(string(n)) {
		return 0, fail("invalid_number")
	}
	v, err := n.Int64()
	if err != nil || v < minimum || v > maximum {
		return 0, fail("invalid_number")
	}
	return v, nil
}
func encoded(value any, size int) ([]byte, error) {
	s, ok := text(value)
	if !ok || len(s) > MaxPayload*2 {
		return nil, fail("invalid_encoding")
	}
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil || len(b) > MaxPayload || (size >= 0 && len(b) != size) || bytes.ContainsAny([]byte(s), "\r\n") {
		return nil, fail("invalid_encoding")
	}
	return b, nil
}
func exact(m map[string]any, required []string, optional ...string) bool {
	allowed := map[string]bool{}
	for _, k := range required {
		if _, ok := m[k]; !ok {
			return false
		}
		allowed[k] = true
	}
	for _, k := range optional {
		allowed[k] = true
	}
	for k := range m {
		if !allowed[k] {
			return false
		}
	}
	return true
}

type Binding struct {
	KeyID, VehicleID, SourceID, Kind string
	PublicKey                        ed25519.PublicKey
	Priority                         int64
	TTL                              map[string]int64
	Units                            map[string]string
	ChannelHash                      string
}
type Policy struct {
	Sources map[string]Binding
	Legacy  *Binding
}

func LegacyPolicy(vehicleID string) *Policy {
	p := &Policy{Sources: map[string]Binding{}}
	if vehicleID != "" {
		p.Legacy = &Binding{KeyID: "unsigned-simulator", VehicleID: vehicleID, SourceID: "val-simulator", Kind: "simulated",
			TTL: map[string]int64{"*": 180000}, Units: map[string]string{"speed_kmh": "km/h", "battery": "%", "hvac_temp": "degC", "cabin_temp": "degC", "volume": "%"}}
	}
	return p
}

func LoadPolicy() (*Policy, error) {
	profile, err := deployprofile.Resolve(deployprofile.OSEnv)
	if err != nil {
		return nil, fail("invalid_deploy_profile")
	}
	legacy := LegacyVehicle
	if profile == deployprofile.Prod {
		legacy = ""
	}
	return ParsePolicy(os.Getenv("VEHICLE_STATE_TRUST"), legacy)
}

func ParsePolicy(raw, legacyVehicle string) (*Policy, error) {
	if raw == "" {
		return LegacyPolicy(legacyVehicle), nil
	}
	if len(raw) > MaxPayload {
		return nil, fail("trust_too_large")
	}
	m, err := decode([]byte(raw))
	if err != nil {
		return nil, err
	}
	v, err := integer(m["version"], 1, 1)
	if err != nil || v != 1 || !exact(m, []string{"version", "sources"}) {
		return nil, fail("invalid_trust_version")
	}
	rows, ok := m["sources"].([]any)
	if !ok || len(rows) < 1 || len(rows) > 64 {
		return nil, fail("invalid_sources")
	}
	p := &Policy{Sources: map[string]Binding{}}
	identities, priorities, tokens := map[string]bool{}, map[string]bool{}, map[string]string{}
	for _, row := range rows {
		r, err := object(row)
		if err != nil || !exact(r, []string{"key_id", "vehicle_id", "source_id", "kind", "public_key", "priority", "ttl_ms", "units"}, "channel_token_sha256") {
			return nil, fail("invalid_source_binding")
		}
		b := Binding{TTL: map[string]int64{}, Units: map[string]string{}}
		if b.KeyID, err = identifier(r["key_id"]); err != nil {
			return nil, err
		}
		if b.VehicleID, err = identifier(r["vehicle_id"]); err != nil {
			return nil, err
		}
		if b.SourceID, err = identifier(r["source_id"]); err != nil {
			return nil, err
		}
		b.Kind, _ = text(r["kind"])
		if b.Kind != "simulated" && b.Kind != "vehicle" && b.Kind != "sandbox" {
			return nil, fail("invalid_source_binding")
		}
		if b.PublicKey, err = encoded(r["public_key"], ed25519.PublicKeySize); err != nil {
			return nil, err
		}
		if b.Priority, err = integer(r["priority"], 0, 1000); err != nil {
			return nil, err
		}
		ttl, err := object(r["ttl_ms"])
		if err != nil || len(ttl) < 1 || len(ttl) > MaxSignals {
			return nil, fail("invalid_ttl_policy")
		}
		units, err := object(r["units"])
		if err != nil || len(units) > MaxSignals {
			return nil, fail("invalid_units")
		}
		for key, value := range ttl {
			if key != "*" && !keyPattern.MatchString(key) {
				return nil, fail("invalid_signal_key")
			}
			age, err := integer(value, 1, 86400000)
			if err != nil {
				return nil, err
			}
			b.TTL[key] = age
		}
		for key, value := range units {
			unit, ok := text(value)
			if !ok || !keyPattern.MatchString(key) || utf8.RuneCountInString(unit) > 32 {
				return nil, fail("invalid_units")
			}
			b.Units[key] = unit
		}
		if b.Kind != "simulated" {
			for key := range b.TTL {
				if _, ok := b.Units[key]; !ok || key == "*" {
					return nil, fail("real_source_requires_signal_policy")
				}
			}
		}
		if rawHash, ok := r["channel_token_sha256"]; ok {
			b.ChannelHash, ok = text(rawHash)
			if !ok || (b.ChannelHash != "" && !hashPattern.MatchString(b.ChannelHash)) {
				return nil, fail("invalid_channel_binding")
			}
			if other, exists := tokens[b.ChannelHash]; b.ChannelHash != "" && exists && other != b.VehicleID {
				return nil, fail("ambiguous_channel_binding")
			}
			if b.ChannelHash != "" {
				tokens[b.ChannelHash] = b.VehicleID
			}
		}
		identity := b.VehicleID + "\x00" + b.SourceID
		priority := b.VehicleID + "\x00" + strconv.FormatInt(b.Priority, 10)
		if _, exists := p.Sources[b.KeyID]; exists || identities[identity] {
			return nil, fail("ambiguous_source_identity")
		}
		if priorities[priority] {
			return nil, fail("ambiguous_source_priority")
		}
		identities[identity], priorities[priority] = true, true
		p.Sources[b.KeyID] = b
	}
	return p, nil
}

func ChannelTokenDigest(token string) string {
	sum := sha256.Sum256([]byte("cockpit.vehicle-channel.v1\x00" + token))
	return hex.EncodeToString(sum[:])
}
func (p *Policy) ChannelVehicleAllowed(token, vehicle string) bool {
	if len(p.Sources) == 0 {
		return p.Legacy != nil && vehicle == p.Legacy.VehicleID
	}
	if token == "" {
		return false
	}
	digest := ChannelTokenDigest(token)
	for _, b := range p.Sources {
		if b.ChannelHash == digest && b.VehicleID == vehicle {
			return true
		}
	}
	return false
}

type Clock func() (wallMS, monotonicMS int64)
type cell struct {
	Value                    any
	Quality, Unit, Operation string
	Observed, Received, Seq  int64
	ExpiresAt                *int64
	ExpiresMono              int64
}
type stream struct {
	Binding       Binding
	Authenticated bool
	Epoch         string
	Started, Seq  int64
	Cells         map[string]cell
}
type Store struct {
	mu        sync.Mutex
	policy    *Policy
	now       Clock
	started   int64
	legacySeq int64
	streams   map[string]*stream
}
type Result struct {
	Accepted                    bool
	Reason, VehicleID, SourceID string
}

func NewStore(policy *Policy, now Clock) *Store {
	if now == nil {
		start := time.Now()
		now = func() (int64, int64) { return time.Now().UnixMilli(), time.Since(start).Milliseconds() }
	}
	wall, _ := now()
	// Keep the admitted trust and TTL maps independent of the caller's maps.
	cloneBinding := func(b Binding) Binding {
		b.PublicKey = append(ed25519.PublicKey(nil), b.PublicKey...)
		ttl, units := map[string]int64{}, map[string]string{}
		for k, v := range b.TTL {
			ttl[k] = v
		}
		for k, v := range b.Units {
			units[k] = v
		}
		b.TTL, b.Units = ttl, units
		return b
	}
	copyPolicy := &Policy{Sources: map[string]Binding{}}
	if policy != nil {
		for k, b := range policy.Sources {
			copyPolicy.Sources[k] = cloneBinding(b)
		}
		if policy.Legacy != nil {
			b := cloneBinding(*policy.Legacy)
			copyPolicy.Legacy = &b
		}
	}
	return &Store{policy: copyPolicy, now: now, started: wall, streams: map[string]*stream{}}
}

func (s *Store) unwrap(event map[string]any, now int64) (Binding, bool, map[string]any, bool, error) {
	bad := func(code string) (Binding, bool, map[string]any, bool, error) {
		return Binding{}, false, nil, false, fail(code)
	}
	if _, present := event["version"]; !present {
		for _, key := range []string{"vehicle_id", "source_id", "source_epoch", "payload", "signature", "key_id"} {
			if _, ok := event[key]; ok {
				return bad("invalid_legacy_event")
			}
		}
		if s.policy.Legacy == nil {
			return bad("legacy_disabled")
		}
		b := *s.policy.Legacy
		rows, ok := event["changes"].([]any)
		if !ok || len(rows) < 1 || len(rows) > MaxSignals {
			return bad("invalid_signals")
		}
		observed, present := event["ts"]
		if !present {
			observed = json.Number(strconv.FormatInt(now, 10))
		}
		samples := []any{}
		for _, row := range rows {
			r, err := object(row)
			if err != nil {
				return bad("invalid_signals")
			}
			key, _ := text(r["key"])
			samples = append(samples, map[string]any{"key": r["key"], "value": r["new"], "observed_at_ms": observed, "quality": "good", "unit": b.Units[key]})
		}
		s.legacySeq++
		data := map[string]any{"vehicle_id": b.VehicleID, "source_id": b.SourceID, "source_epoch": "legacy", "epoch_started_at_ms": json.Number("0"),
			"source_seq": json.Number(string(mustJSON(s.legacySeq))), "emitted_at_ms": observed, "snapshot": true, "signals": samples}
		return b, false, data, true, nil
	}
	v, err := integer(event["version"], 2, 2)
	if err != nil || v != 2 {
		return bad("unsupported_state_version")
	}
	kid, err := identifier(event["key_id"])
	if err != nil {
		return bad("invalid_identity")
	}
	raw, err := encoded(event["payload"], -1)
	if err != nil {
		return bad("invalid_encoding")
	}
	b, authenticated := s.policy.Sources[kid]
	if authenticated {
		sig, err := encoded(event["signature"], ed25519.SignatureSize)
		if err != nil || !ed25519.Verify(b.PublicKey, append([]byte(domain), raw...), sig) {
			return bad("invalid_state_signature")
		}
	} else {
		sig, ok := text(event["signature"])
		if s.policy.Legacy == nil || kid != s.policy.Legacy.KeyID || !ok || sig != "" {
			return bad("unknown_state_source")
		}
		b = *s.policy.Legacy
	}
	data, err := decode(raw)
	if err != nil {
		return bad("invalid_json")
	}
	return b, authenticated, data, false, nil
}

func normalizedValue(value any, depth int) (any, error) {
	if depth > 6 {
		return nil, fail("value_too_deep")
	}
	switch x := value.(type) {
	case nil, bool:
		return x, nil
	case json.Number:
		f, err := x.Float64()
		if err != nil || math.IsInf(f, 0) || math.IsNaN(f) || (integerPattern.MatchString(string(x)) && math.Abs(f) > 9007199254740991) {
			return nil, fail("invalid_value")
		}
		return f, nil
	case string:
		if utf8.RuneCountInString(x) <= 2048 {
			return x, nil
		}
	case []any:
		if len(x) > 64 {
			return nil, fail("invalid_value")
		}
		out := make([]any, len(x))
		for i, v := range x {
			n, err := normalizedValue(v, depth+1)
			if err != nil {
				return nil, err
			}
			out[i] = n
		}
		return out, nil
	case map[string]any:
		if len(x) > 64 {
			return nil, fail("invalid_value")
		}
		out := map[string]any{}
		for k, v := range x {
			if utf8.RuneCountInString(k) > 128 {
				return nil, fail("invalid_value_key")
			}
			n, err := normalizedValue(v, depth+1)
			if err != nil {
				return nil, err
			}
			out[k] = n
		}
		return out, nil
	}
	return nil, fail("invalid_value")
}

func knownSignal(key string, value any, quality string) bool {
	if quality != "good" {
		return true
	}
	switch key {
	case "speed_kmh", "battery", "cabin_temp", "hvac_temp", "volume":
		v, ok := value.(float64)
		if !ok {
			return false
		}
		if (key == "speed_kmh" || key == "battery" || key == "volume") && v < 0 {
			return false
		}
		if (key == "battery" || key == "volume") && v > 100 {
			return false
		}
	case "gear":
		v, ok := value.(string)
		return ok && v != ""
	case "child_lock":
		_, ok := value.(bool)
		return ok
	}
	return true
}

func (s *Store) Ingest(raw []byte) Result {
	s.mu.Lock()
	defer s.mu.Unlock()
	bad := func(code string) Result { return Result{Reason: code} }
	if len(raw) > MaxPayload*2 {
		return bad("event_too_large")
	}
	event, err := decode(raw)
	if err != nil {
		return bad("invalid_json")
	}
	now, mono := s.now()
	b, authenticated, p, legacy, err := s.unwrap(event, now)
	if err != nil {
		return bad(err.Error())
	}
	if !exact(p, []string{"vehicle_id", "source_id", "source_epoch", "epoch_started_at_ms", "source_seq", "emitted_at_ms", "snapshot", "signals"}) {
		return bad("invalid_state_fields")
	}
	vid, err := identifier(p["vehicle_id"])
	if err != nil {
		return bad("invalid_identity")
	}
	sid, err := identifier(p["source_id"])
	if err != nil {
		return bad("invalid_identity")
	}
	epoch, err := identifier(p["source_epoch"])
	if err != nil {
		return bad("invalid_identity")
	}
	if vid != b.VehicleID || sid != b.SourceID {
		return bad("state_identity_mismatch")
	}
	started, err := integer(p["epoch_started_at_ms"], 0, 9007199254740991)
	if err != nil {
		return bad("invalid_number")
	}
	emitted, err := integer(p["emitted_at_ms"], 0, 9007199254740991)
	if err != nil {
		return bad("invalid_number")
	}
	seq, err := integer(p["source_seq"], 1, 9007199254740991)
	if err != nil {
		return bad("invalid_number")
	}
	if started > emitted || emitted > now+ClockSkewMS {
		return bad("invalid_source_clock")
	}
	if !legacy && emitted+ClockSkewMS < s.started {
		return bad("predates_receiver")
	}
	snapshot, ok := p["snapshot"].(bool)
	if !ok {
		return bad("invalid_signals")
	}
	rows, ok := p["signals"].([]any)
	if !ok || len(rows) > MaxSignals {
		return bad("invalid_signals")
	}
	key := vid + "\x00" + sid
	previous := s.streams[key]
	replacement := previous == nil || previous.Epoch != epoch
	if replacement && !snapshot {
		return bad("snapshot_required")
	}
	if previous != nil {
		if previous.Epoch == epoch && (started != previous.Started || seq <= previous.Seq) {
			return bad("replayed_state")
		}
		if previous.Epoch != epoch && started <= previous.Started {
			return bad("retired_epoch")
		}
	}
	cells := map[string]cell{}
	for _, row := range rows {
		r, err := object(row)
		if err != nil || !exact(r, []string{"key", "value", "observed_at_ms", "quality", "unit"}, "operation_id") {
			return bad("invalid_signal_fields")
		}
		name, ok := text(r["key"])
		if !ok || !keyPattern.MatchString(name) {
			return bad("invalid_signal_key")
		}
		if _, exists := cells[name]; exists {
			return bad("invalid_signal_key")
		}
		quality, ok := text(r["quality"])
		if !ok || (quality != "good" && quality != "uncertain" && quality != "unavailable") {
			return bad("invalid_quality")
		}
		unit, ok := text(r["unit"])
		if !ok || unit != b.Units[name] {
			return bad("signal_unit_mismatch")
		}
		observed, err := integer(r["observed_at_ms"], 0, 9007199254740991)
		if err != nil {
			return bad("invalid_number")
		}
		if observed > emitted+ClockSkewMS || observed > now+ClockSkewMS {
			return bad("invalid_signal_clock")
		}
		ttl, ok := b.TTL[name]
		if !ok {
			ttl, ok = b.TTL["*"]
		}
		if !ok {
			return bad("signal_not_enrolled")
		}
		value, err := normalizedValue(r["value"], 0)
		if err != nil {
			return bad(err.Error())
		}
		if !knownSignal(name, value, quality) {
			return bad("invalid_signal_value")
		}
		operation := ""
		if v, present := r["operation_id"]; present {
			operation, ok = text(v)
			if !ok || (operation != "" && !idPattern.MatchString(operation)) {
				return bad("invalid_operation_reference")
			}
		}
		age := now - observed
		if age < 0 {
			age = 0
		}
		remaining := ttl - age
		if remaining < 0 {
			remaining = 0
		}
		c := cell{Value: value, Quality: quality, Unit: unit, Observed: observed, Received: now, Seq: seq, ExpiresMono: math.MaxInt64, Operation: operation}
		if ttl > 0 {
			expires := now + remaining
			c.ExpiresAt = &expires
			c.ExpiresMono = mono + remaining
		}
		cells[name] = c
	}
	if !replacement {
		n := len(previous.Cells)
		for key := range cells {
			if _, exists := previous.Cells[key]; !exists {
				n++
			}
		}
		if n > MaxSignals {
			return bad("too_many_signals")
		}
	} else {
		previous = &stream{Binding: b, Authenticated: authenticated, Epoch: epoch, Started: started, Cells: map[string]cell{}}
	}
	previous.Seq = seq
	for name, c := range cells {
		old, exists := previous.Cells[name]
		if !exists || c.Observed >= old.Observed {
			if exists && c.Observed == old.Observed {
				if c.ExpiresMono > old.ExpiresMono {
					c.ExpiresMono = old.ExpiresMono
				}
				c.ExpiresAt = old.ExpiresAt
			}
			previous.Cells[name] = c
		}
	}
	s.streams[key] = previous
	return Result{Accepted: true, VehicleID: vid, SourceID: sid}
}

func mustJSON(value any) []byte { b, _ := json.Marshal(value); return b }
func clone(value any) any       { var out any; _ = json.Unmarshal(mustJSON(value), &out); return out }

func (s *Store) View(vehicleID string) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	type candidate struct {
		stream *stream
		cell   cell
	}
	chosen := map[string]candidate{}
	for _, st := range s.streams {
		if st.Binding.VehicleID != vehicleID {
			continue
		}
		for key, c := range st.Cells {
			old, exists := chosen[key]
			if !exists || (st.Authenticated && !old.stream.Authenticated) || (st.Authenticated == old.stream.Authenticated && st.Binding.Priority > old.stream.Binding.Priority) {
				chosen[key] = candidate{st, c}
			}
		}
	}
	_, mono := s.now()
	values, meta := map[string]any{}, map[string]any{}
	for key, entry := range chosen {
		st, c := entry.stream, entry.cell
		shadowed := false
		for _, authority := range s.policy.Sources {
			_, explicit := authority.TTL[key]
			_, wildcard := authority.TTL["*"]
			if authority.VehicleID == vehicleID && authority.Priority > st.Binding.Priority && (explicit || wildcard) {
				shadowed = true
				break
			}
		}
		if shadowed {
			continue
		}
		status := c.Quality
		if mono >= c.ExpiresMono {
			status = "stale"
		}
		freshness := "bounded"
		if c.ExpiresAt == nil {
			freshness = "legacy-unbounded"
		}
		if status == "good" {
			values[key] = clone(c.Value)
		}
		meta[key] = map[string]any{"quality": status, "unit": c.Unit, "source_id": st.Binding.SourceID, "source_kind": st.Binding.Kind, "authenticated": st.Authenticated,
			"freshness": freshness, "source_epoch": st.Epoch, "source_seq": c.Seq, "observed_at_ms": c.Observed, "received_at_ms": c.Received, "expires_at_ms": c.ExpiresAt, "operation_id": c.Operation}
	}
	return map[string]any{"version": 2, "vehicle_id": vehicleID, "state": values, "signals": meta}
}
func (s *Store) Snapshot(vehicleID string) map[string]any {
	return s.View(vehicleID)["state"].(map[string]any)
}
func (s *Store) Vehicles() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	set := map[string]bool{}
	for _, st := range s.streams {
		set[st.Binding.VehicleID] = true
	}
	values := []string{}
	for key := range set {
		values = append(values, key)
	}
	sort.Strings(values)
	return values
}
