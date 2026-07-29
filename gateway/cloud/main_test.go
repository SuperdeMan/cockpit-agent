package main

import (
	"context"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"strconv"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	agentpb "github.com/cockpit/car-agent/gen/go/cockpit/agent/v1"
	channelpb "github.com/cockpit/car-agent/gen/go/cockpit/channel/v1"
	commonpb "github.com/cockpit/car-agent/gen/go/cockpit/common/v1"
	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

type fakeDownSender struct {
	frames chan *channelpb.DownFrame
	err    error
}

func (f *fakeDownSender) Send(frame *channelpb.DownFrame) error {
	if f.err != nil {
		return f.err
	}
	f.frames <- frame
	return nil
}

func TestDispatchEdgeCallRejectsMissingVehicleStream(t *testing.T) {
	server := &channelServer{}

	_, err := server.dispatchEdgeCall(
		context.Background(), "missing", &channelpb.EdgeCall{StepId: "s1"})

	if status.Code(err) != codes.NotFound {
		t.Fatalf("expected NotFound, got %v", err)
	}
}

func TestDispatchEdgeCallPairsResultByCorrelationID(t *testing.T) {
	server := &channelServer{}
	sender := &fakeDownSender{frames: make(chan *channelpb.DownFrame, 1)}
	server.sessions.Store("v1", &sessionState{
		vehicleID: "v1",
		sender:    &sendMu{stream: sender},
	})

	go func() {
		frame := <-sender.frames
		if frame.GetEdgeCall().GetStepId() != "s1" {
			t.Errorf("unexpected edge call: %v", frame)
		}
		server.deliverEdgeResult(frame.GetCorrelationId(), &channelpb.EdgeResult{
			StepId: "s1",
			Result: &agentpb.ExecuteResponse{
				Status: agentpb.ExecuteResponse_OK,
				Speech: "done",
			},
		})
	}()

	result, err := server.dispatchEdgeCall(
		context.Background(), "v1", &channelpb.EdgeCall{StepId: "s1"})
	if err != nil {
		t.Fatalf("dispatch failed: %v", err)
	}
	if result.GetResult().GetSpeech() != "done" {
		t.Fatalf("unexpected result: %v", result)
	}
}

func TestDispatchEdgeCallHonorsContextDeadline(t *testing.T) {
	server := &channelServer{}
	sender := &fakeDownSender{frames: make(chan *channelpb.DownFrame, 1)}
	server.sessions.Store("v1", &sessionState{
		vehicleID: "v1",
		sender:    &sendMu{stream: sender},
	})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()

	_, err := server.dispatchEdgeCall(
		ctx, "v1", &channelpb.EdgeCall{StepId: "s1"})

	if status.Code(err) != codes.DeadlineExceeded {
		t.Fatalf("expected DeadlineExceeded, got %v", err)
	}
}

func TestBindRequestVehicleRejectsCrossVehicleRequest(t *testing.T) {
	req := &orchpb.HandleRequest{
		Context: &commonpb.ContextRef{VehicleId: "v2"},
	}

	err := bindRequestVehicle(req, "v1")

	if status.Code(err) != codes.PermissionDenied {
		t.Fatalf("expected PermissionDenied, got %v", err)
	}
}

func TestBindRequestVehicleFillsAuthenticatedStreamVehicle(t *testing.T) {
	req := &orchpb.HandleRequest{}

	if err := bindRequestVehicle(req, "v1"); err != nil {
		t.Fatalf("bind failed: %v", err)
	}
	if req.GetContext().GetVehicleId() != "v1" {
		t.Fatalf("unexpected vehicle id: %s", req.GetContext().GetVehicleId())
	}
}

// ─── R3.1 层 2：通道鉴权（Hello session_token）───

func TestChannelTokenAllowed(t *testing.T) {
	// AUTH_REQUIRED=false → 恒放行（保持现状）。
	open := &channelServer{authRequired: false}
	if !open.channelTokenAllowed("") || !open.channelTokenAllowed("whatever") {
		t.Fatalf("auth off should allow any token")
	}
	// AUTH_REQUIRED=true → token 须非空且在允许集内。
	secured := &channelServer{authRequired: true, channelTokens: parseChannelTokens("a, b ,c")}
	if !secured.channelTokenAllowed("a") || !secured.channelTokenAllowed("b") {
		t.Fatalf("valid token should be allowed")
	}
	if secured.channelTokenAllowed("") || secured.channelTokenAllowed("x") {
		t.Fatalf("empty/unknown token should be rejected when required")
	}
}

// fakeConnectStream 是 EdgeCloudChannel_ConnectServer 的最小实现：按序回放 Recv 帧、收集 Send。
// 嵌入 grpc.ServerStream 只为满足接口——Connect 的 Hello 路径不调其余方法（调则 panic 暴露）。
type fakeConnectStream struct {
	grpc.ServerStream
	recv []*channelpb.UpFrame
	idx  int
	sent []*channelpb.DownFrame
}

func (f *fakeConnectStream) Recv() (*channelpb.UpFrame, error) {
	if f.idx >= len(f.recv) {
		return nil, io.EOF
	}
	fr := f.recv[f.idx]
	f.idx++
	return fr, nil
}

func (f *fakeConnectStream) Send(fr *channelpb.DownFrame) error {
	f.sent = append(f.sent, fr)
	return nil
}

func helloFrame(vehicle, token string) *channelpb.UpFrame {
	return &channelpb.UpFrame{
		CorrelationId: "c1",
		Body: &channelpb.UpFrame_Hello{
			Hello: &channelpb.Hello{VehicleId: vehicle, SessionToken: token},
		},
	}
}

func TestConnectRejectsInvalidChannelToken(t *testing.T) {
	s := &channelServer{authRequired: true, channelTokens: parseChannelTokens("good")}
	stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame("v1", "bad")}}

	_ = s.Connect(stream) // Hello 校验失败 → 发 hello_ack 后 return

	if len(stream.sent) != 1 {
		t.Fatalf("want 1 hello_ack, got %d", len(stream.sent))
	}
	ack := stream.sent[0].GetHelloAck()
	if ack == nil || ack.Ok {
		t.Fatalf("want hello_ack ok=false, got %+v", ack)
	}
}

func TestConnectAcceptsValidChannelToken(t *testing.T) {
	s := &channelServer{authRequired: true, channelTokens: parseChannelTokens("good")}
	// 第一帧有效 Hello；第二帧缺省 → Recv 返回 io.EOF → Connect 正常收尾。
	stream := &fakeConnectStream{recv: []*channelpb.UpFrame{helloFrame("v1", "good")}}

	if err := s.Connect(stream); err != nil {
		t.Fatalf("connect returned error: %v", err)
	}
	if len(stream.sent) != 1 {
		t.Fatalf("want 1 hello_ack, got %d", len(stream.sent))
	}
	ack := stream.sent[0].GetHelloAck()
	if ack == nil || !ack.Ok {
		t.Fatalf("want hello_ack ok=true, got %+v", ack)
	}
}

type dependencyKind uint8

const (
	dependencyUnknown dependencyKind = iota
	dependencyServer
	dependencyPlanner
	dependencyIdempotency
	dependencyPlannerHandle
	dependencyIdempotencyMark
)

type dependencyModel struct {
	serverType        string
	plannerFields     map[string]bool
	idempotencyFields map[string]bool
	plannerAccessors  map[string]bool
	statusAliases     map[string]bool
	codesAliases      map[string]bool
}

type dependencyCall struct {
	call       *ast.CallExpr
	assignment *ast.AssignStmt
	enclosing  []*ast.IfStmt
}

func receiverIdentity(fn *ast.FuncDecl) (string, string, bool) {
	if fn.Recv == nil || len(fn.Recv.List) != 1 || len(fn.Recv.List[0].Names) != 1 {
		return "", "", false
	}
	name := fn.Recv.List[0].Names[0].Name
	expr := fn.Recv.List[0].Type
	if pointer, ok := expr.(*ast.StarExpr); ok {
		expr = pointer.X
	}
	ident, ok := expr.(*ast.Ident)
	if !ok {
		return "", "", false
	}
	return name, ident.Name, true
}

func terminalTypeName(expr ast.Expr) string {
	switch value := expr.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.SelectorExpr:
		return value.Sel.Name
	case *ast.StarExpr:
		return terminalTypeName(value.X)
	case *ast.IndexExpr:
		return terminalTypeName(value.X)
	case *ast.IndexListExpr:
		return terminalTypeName(value.X)
	default:
		return ""
	}
}

func typedDependencyFields(file *ast.File, serverType string) (map[string]bool, map[string]bool) {
	planner := map[string]bool{}
	idempotency := map[string]bool{}
	for _, decl := range file.Decls {
		gen, ok := decl.(*ast.GenDecl)
		if !ok || gen.Tok != token.TYPE {
			continue
		}
		for _, spec := range gen.Specs {
			typeSpec, ok := spec.(*ast.TypeSpec)
			if !ok || typeSpec.Name.Name != serverType {
				continue
			}
			structType, ok := typeSpec.Type.(*ast.StructType)
			if !ok {
				continue
			}
			for _, field := range structType.Fields.List {
				for _, name := range field.Names {
					switch terminalTypeName(field.Type) {
					case "CloudPlannerClient":
						planner[name.Name] = true
					case "IdempotencyStore":
						idempotency[name.Name] = true
					}
				}
			}
		}
	}
	return planner, idempotency
}

func importAliases(file *ast.File, importPath, defaultName string) map[string]bool {
	aliases := map[string]bool{}
	for _, spec := range file.Imports {
		path, err := strconv.Unquote(spec.Path.Value)
		if err != nil || path != importPath {
			continue
		}
		name := defaultName
		if spec.Name != nil {
			name = spec.Name.Name
		}
		if name != "_" && name != "." {
			aliases[name] = true
		}
	}
	return aliases
}

func plannerAccessorMethods(file *ast.File, serverType string, plannerFields map[string]bool) map[string]bool {
	accessors := map[string]bool{}
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			continue
		}
		receiverName, receiverType, ok := receiverIdentity(fn)
		if !ok || receiverType != serverType {
			continue
		}
		ast.Inspect(fn.Body, func(node ast.Node) bool {
			if _, nested := node.(*ast.FuncLit); nested {
				return false
			}
			ret, ok := node.(*ast.ReturnStmt)
			if !ok {
				return true
			}
			for _, result := range ret.Results {
				selector, ok := result.(*ast.SelectorExpr)
				if !ok {
					continue
				}
				receiver, receiverOK := selector.X.(*ast.Ident)
				if receiverOK && receiver.Name == receiverName &&
					plannerFields[selector.Sel.Name] {
					accessors[fn.Name.Name] = true
				}
			}
			return true
		})
	}
	return accessors
}

func resolveDependency(expr ast.Expr, bindings map[string]dependencyKind, model dependencyModel) dependencyKind {
	switch value := expr.(type) {
	case *ast.Ident:
		return bindings[value.Name]
	case *ast.ParenExpr:
		return resolveDependency(value.X, bindings, model)
	case *ast.SelectorExpr:
		base := resolveDependency(value.X, bindings, model)
		if base == dependencyServer && model.plannerFields[value.Sel.Name] {
			return dependencyPlanner
		}
		if base == dependencyServer && model.idempotencyFields[value.Sel.Name] {
			return dependencyIdempotency
		}
		if base == dependencyPlanner && value.Sel.Name == "Handle" {
			return dependencyPlannerHandle
		}
		if base == dependencyIdempotency && value.Sel.Name == "MarkIfNew" {
			return dependencyIdempotencyMark
		}
	case *ast.CallExpr:
		selector, ok := value.Fun.(*ast.SelectorExpr)
		if ok && resolveDependency(selector.X, bindings, model) == dependencyServer &&
			model.plannerAccessors[selector.Sel.Name] {
			return dependencyPlanner
		}
	}
	return dependencyUnknown
}

func bindDependencies(assign *ast.AssignStmt, bindings map[string]dependencyKind, model dependencyModel) {
	if len(assign.Lhs) != len(assign.Rhs) {
		return
	}
	for index, lhs := range assign.Lhs {
		ident, ok := lhs.(*ast.Ident)
		if !ok || ident.Name == "_" {
			continue
		}
		kind := resolveDependency(assign.Rhs[index], bindings, model)
		if kind == dependencyUnknown {
			delete(bindings, ident.Name)
		} else {
			bindings[ident.Name] = kind
		}
	}
}

func assignmentContaining(root ast.Node, target *ast.CallExpr) *ast.AssignStmt {
	var found *ast.AssignStmt
	ast.Inspect(root, func(node ast.Node) bool {
		if _, nested := node.(*ast.FuncLit); nested {
			return false
		}
		assign, ok := node.(*ast.AssignStmt)
		if !ok || target.Pos() < assign.Pos() || target.End() > assign.End() {
			return true
		}
		found = assign
		return false
	})
	return found
}

func enclosingIfStatements(root ast.Node, target *ast.CallExpr) []*ast.IfStmt {
	var found []*ast.IfStmt
	ast.Inspect(root, func(node ast.Node) bool {
		if _, nested := node.(*ast.FuncLit); nested {
			return false
		}
		ifStmt, ok := node.(*ast.IfStmt)
		if ok && ifStmt.Pos() < target.Pos() && target.End() < ifStmt.End() {
			found = append(found, ifStmt)
		}
		return true
	})
	return found
}

func collectDependencyCalls(fn *ast.FuncDecl, model dependencyModel) ([]dependencyCall, []dependencyCall) {
	receiverName, _, _ := receiverIdentity(fn)
	bindings := map[string]dependencyKind{receiverName: dependencyServer}
	var handles []dependencyCall
	var marks []dependencyCall
	ast.Inspect(fn.Body, func(node ast.Node) bool {
		if _, nested := node.(*ast.FuncLit); nested {
			// 当前契约只覆盖 handleRequest 本体；未执行闭包不能制造调用或门禁。
			// 若生产改为调用跨函数 retry/幂等 helper，应显式扩展调用图而非按方法名字猜测。
			return false
		}
		if assign, ok := node.(*ast.AssignStmt); ok {
			bindDependencies(assign, bindings, model)
		}
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		record := dependencyCall{
			call:       call,
			assignment: assignmentContaining(fn.Body, call),
			enclosing:  enclosingIfStatements(fn.Body, call),
		}
		switch fun := call.Fun.(type) {
		case *ast.Ident:
			if bindings[fun.Name] == dependencyPlannerHandle {
				handles = append(handles, record)
			}
			if bindings[fun.Name] == dependencyIdempotencyMark {
				marks = append(marks, record)
			}
		case *ast.SelectorExpr:
			kind := resolveDependency(fun.X, bindings, model)
			if fun.Sel.Name == "Handle" && kind == dependencyPlanner {
				handles = append(handles, record)
			}
			if fun.Sel.Name == "MarkIfNew" && kind == dependencyIdempotency {
				marks = append(marks, record)
			}
		}
		return true
	})
	return handles, marks
}

func isStatusCodeOf(expr ast.Expr, errName string, aliases map[string]bool) bool {
	call, ok := expr.(*ast.CallExpr)
	if !ok || len(call.Args) != 1 {
		return false
	}
	selector, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || selector.Sel.Name != "Code" {
		return false
	}
	pkg, ok := selector.X.(*ast.Ident)
	if !ok || !aliases[pkg.Name] {
		return false
	}
	arg, ok := call.Args[0].(*ast.Ident)
	return ok && arg.Name == errName
}

func isUnavailable(expr ast.Expr, aliases map[string]bool) bool {
	selector, ok := expr.(*ast.SelectorExpr)
	if !ok || selector.Sel.Name != "Unavailable" {
		return false
	}
	pkg, ok := selector.X.(*ast.Ident)
	return ok && aliases[pkg.Name]
}

func conditionChecksUnavailable(
	expr ast.Expr,
	errName string,
	statusAliases map[string]bool,
	codesAliases map[string]bool,
) bool {
	found := false
	ast.Inspect(expr, func(n ast.Node) bool {
		compare, ok := n.(*ast.BinaryExpr)
		if !ok || compare.Op != token.EQL {
			return true
		}
		if (isStatusCodeOf(compare.X, errName, statusAliases) &&
			isUnavailable(compare.Y, codesAliases)) ||
			(isUnavailable(compare.X, codesAliases) &&
				isStatusCodeOf(compare.Y, errName, statusAliases)) {
			found = true
			return false
		}
		return true
	})
	return found
}

func validateHandleRequestContract(file *ast.File) error {
	var handleRequest *ast.FuncDecl
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if ok && fn.Name.Name == "handleRequest" && fn.Body != nil {
			handleRequest = fn
			break
		}
	}
	if handleRequest == nil {
		return fmt.Errorf("handleRequest function not found")
	}
	_, serverType, ok := receiverIdentity(handleRequest)
	if !ok {
		return fmt.Errorf("handleRequest must be a receiver method")
	}
	plannerFields, idempotencyFields := typedDependencyFields(file, serverType)
	if len(plannerFields) != 1 || len(idempotencyFields) != 1 {
		return fmt.Errorf(
			"%s must have one CloudPlannerClient and one IdempotencyStore field",
			serverType,
		)
	}
	model := dependencyModel{
		serverType:        serverType,
		plannerFields:     plannerFields,
		idempotencyFields: idempotencyFields,
		plannerAccessors:  plannerAccessorMethods(file, serverType, plannerFields),
		statusAliases: importAliases(
			file, "google.golang.org/grpc/status", "status",
		),
		codesAliases: importAliases(
			file, "google.golang.org/grpc/codes", "codes",
		),
	}
	if len(model.plannerAccessors) == 0 {
		return fmt.Errorf("planner client accessor not found")
	}
	handles, marks := collectDependencyCalls(handleRequest, model)
	if len(marks) != 1 {
		return fmt.Errorf("handleRequest must call MarkIfNew exactly once, got %d", len(marks))
	}
	if len(handles) != 2 {
		return fmt.Errorf("handleRequest must call planner Handle exactly twice, got %d", len(handles))
	}
	if marks[0].call.Pos() > handles[0].call.Pos() {
		return fmt.Errorf("MarkIfNew must guard the first planner Handle call")
	}

	firstAssign := handles[0].assignment
	if firstAssign == nil || len(firstAssign.Lhs) < 2 {
		return fmt.Errorf("first planner Handle call must capture its error")
	}
	firstErr, ok := firstAssign.Lhs[len(firstAssign.Lhs)-1].(*ast.Ident)
	if !ok {
		return fmt.Errorf("first planner Handle error target must be an identifier")
	}

	retryFound := false
	for _, ifStmt := range handles[1].enclosing {
		if ifStmt.Pos() > handles[0].call.Pos() &&
			conditionChecksUnavailable(
				ifStmt.Cond,
				firstErr.Name,
				model.statusAliases,
				model.codesAliases,
			) {
			retryFound = true
			break
		}
	}
	if !retryFound {
		return fmt.Errorf(
			"second planner Handle must be inside the first error's UNAVAILABLE retry branch",
		)
	}
	return nil
}

func validateHandleRequestContractSource(source string) error {
	file, err := parser.ParseFile(
		token.NewFileSet(),
		"main.go",
		source,
		parser.SkipObjectResolution,
	)
	if err != nil {
		return fmt.Errorf("parse main.go: %w", err)
	}
	return validateHandleRequestContract(file)
}

func TestHandleRequestRetryASTContract(t *testing.T) {
	file, err := parser.ParseFile(
		token.NewFileSet(),
		"main.go",
		nil,
		parser.SkipObjectResolution,
	)
	if err != nil {
		t.Fatalf("parse main.go: %v", err)
	}
	if err := validateHandleRequestContract(file); err != nil {
		t.Fatal(err)
	}
}

func TestHandleRequestRetryASTVariants(t *testing.T) {
	const source = `package main
import (
	grpcodes "google.golang.org/grpc/codes"
	grpcstatus "google.golang.org/grpc/status"
)
type CloudPlannerClient interface{}
type IdempotencyStore interface{}
type channelServer struct {
	planner CloudPlannerClient
	dedupe IdempotencyStore
}
func (srv *channelServer) plannerClient() CloudPlannerClient { return srv.planner }
func (srv *channelServer) reconnectPlanner() {}
func (srv *channelServer) handleRequest() {
	var ctx, req, correlation any
	__BODY__
}
`
	const direct = `
	if !srv.dedupe.MarkIfNew(ctx, correlation, 0) { return }
	stream, problem := srv.plannerClient().Handle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		srv.reconnectPlanner()
		stream, problem = srv.plannerClient().Handle(ctx, req)
	}
	_ = stream
`
	cases := []struct {
		name    string
		body    string
		wantErr bool
	}{
		{
			name: "unrelated calls in nested closure are ignored",
			body: direct + `
	_ = func() {
		var unrelated interface{ Handle() }
		unrelated.Handle()
		var other interface{ MarkIfNew() }
		other.MarkIfNew()
	}
`,
		},
		{
			name: "planner receiver local aliases are followed",
			body: `
	if !srv.dedupe.MarkIfNew(ctx, correlation, 0) { return }
	planner := srv.plannerClient()
	stream, problem := planner.Handle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		srv.reconnectPlanner()
		planner = srv.plannerClient()
		stream, problem = planner.Handle(ctx, req)
	}
	_ = stream
`,
		},
		{
			name: "planner method value aliases are followed",
			body: `
	if !srv.dedupe.MarkIfNew(ctx, correlation, 0) { return }
	firstHandle := srv.plannerClient().Handle
	stream, problem := firstHandle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		srv.reconnectPlanner()
		retryHandle := srv.plannerClient().Handle
		stream, problem = retryHandle(ctx, req)
	}
	_ = stream
`,
		},
		{
			name: "idempotency local alias is followed",
			body: `
	gate := srv.dedupe
	if !gate.MarkIfNew(ctx, correlation, 0) { return }
	stream, problem := srv.plannerClient().Handle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		srv.reconnectPlanner()
		stream, problem = srv.plannerClient().Handle(ctx, req)
	}
	_ = stream
`,
		},
		{
			name: "server receiver local alias is followed",
			body: `
	server := srv
	if !server.dedupe.MarkIfNew(ctx, correlation, 0) { return }
	stream, problem := server.plannerClient().Handle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		server.reconnectPlanner()
		stream, problem = server.plannerClient().Handle(ctx, req)
	}
	_ = stream
`,
		},
		{
			name:    "second direct idempotency gate is rejected",
			body:    direct + "\nsrv.dedupe.MarkIfNew(ctx, correlation, 0)\n",
			wantErr: true,
		},
		{
			name: "second idempotency gate through alias is rejected",
			body: direct + `
	gate := srv.dedupe
	gate.MarkIfNew(ctx, correlation, 0)
`,
			wantErr: true,
		},
		{
			name: "second idempotency method value is rejected",
			body: direct + `
	secondMark := srv.dedupe.MarkIfNew
	secondMark(ctx, correlation, 0)
`,
			wantErr: true,
		},
		{
			name: "unrelated mainline Handle cannot replace planner retry",
			body: `
	if !srv.dedupe.MarkIfNew(ctx, correlation, 0) { return }
	stream, problem := srv.plannerClient().Handle(ctx, req)
	if problem != nil && grpcstatus.Code(problem) == grpcodes.Unavailable {
		var unrelated interface{ Handle(any, any) (any, error) }
		stream, problem = unrelated.Handle(ctx, req)
	}
	_ = stream
`,
			wantErr: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := validateHandleRequestContractSource(
				strings.Replace(source, "__BODY__", tc.body, 1),
			)
			if tc.wantErr && err == nil {
				t.Fatal("expected contract violation")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("unexpected contract violation: %v", err)
			}
		})
	}
}
