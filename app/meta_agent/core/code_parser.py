import ast
from typing import List, Set, Optional, Any, Dict
from abc import ABC, abstractmethod

class ProgramNode(ABC):
    """Base class for all graph nodes"""
    
    def __init__(self, node_id: int):
        self.node_id = node_id
    
    @abstractmethod
    def __str__(self):
        pass

class ProgramScope:
    
    def __init__(self, parent_node: ProgramNode, nodes: List[ProgramNode] = None):
        self.nodes: List[ProgramNode] = [] if nodes is None else nodes
        self.parent_node: ProgramNode = parent_node

    def __str__(self):
        return "ProgramScope with {} nodes\n    -> [{}]".format(len(self.nodes), ', '.join(str(node.node_id) for node in self.nodes))


class InstructionNode(ProgramNode):
    """Node for non-monitored instructions"""
    
    def __init__(self, node_id: int, instructions: List[str]):
        super().__init__(node_id)
        self.instructions = instructions
    
    def __str__(self):
        return f"InstructionNode({self.node_id}): {'; '.join(self.instructions)}"

class MonitoredFunctionNode(ProgramNode):
    """Node for monitored function calls"""
    
    def __init__(self, node_id: int, function_name: str, args: List[str], kwargs: Dict[str, str]):
        super().__init__(node_id)
        self.function_name = function_name
        self.args = args
        self.kwargs = kwargs
    
    def __str__(self):
        args_str = ", ".join(self.args)
        kwargs_str = ", ".join([f"{k}={v}" for k, v in self.kwargs.items()])
        all_args = ", ".join(filter(None, [args_str, kwargs_str]))
        return f"MonitoredFunctionNode({self.node_id}): {self.function_name}({all_args})"

class BranchNode(ProgramNode):
    """Node for conditional branching"""
    
    def __init__(self, node_id: int, condition: str):
        super().__init__(node_id)
        self.condition = condition
        self.true_branch: ProgramScope = None 
        self.false_branch: Optional[ProgramScope] = None
    
    def set_branches(self, true_branch: ProgramScope, false_branch: Optional[ProgramScope] = None):
        assert true_branch.parent_node is self
        if false_branch:
            assert false_branch.parent_node is self
        self.true_branch = true_branch
        self.false_branch = false_branch
    
    def __str__(self):
        return f"BranchNode({self.node_id}):\nif {self.condition}:\n\t->{self.true_branch}\nelse:\n\t-> {self.false_branch}"

class LoopNode(ProgramNode):
    """Node for loops with initialization and stop condition"""
    
    def __init__(self, node_id: int, loop_type: str, initialization: str = "", 
                 condition: str = "", update: str = ""):
        super().__init__(node_id)
        self.loop_type = loop_type  # 'for', 'while'
        assert self.loop_type == 'while'
        self.initialization = initialization
        self.condition = condition
        self.update = update
        self.body: Optional[ProgramScope] = None
        self.exit: Optional[ProgramScope] = None
    
    def set_body_and_exit(self, body: ProgramScope, exit_node: Optional[ProgramScope] = None):
        self.body = body
        self.exit = exit_node
    
    def __str__(self):
        if self.loop_type == 'for':
            return f"LoopNode({self.node_id}): for {self.initialization} in {self.condition}"
        else:
            return f"LoopNode({self.node_id}):\nwhile {self.condition}:\n\t-> {self.body}\nelse:\n\t-> {self.exit}"

class IterationNode(ProgramNode):
    """Node for iteration over arrays with index and item access"""
    
    def __init__(self, node_id: int, target_var: str, iterable: str):
        super().__init__(node_id)
        self.target_var = target_var
        self.iterable = iterable
        self.body: Optional[ProgramScope] = None
        self.exit: Optional[ProgramScope] = None
    
    def set_body_and_exit(self, body: ProgramScope, exit_node: Optional[ProgramScope] = None):
        self.body = body
        self.exit = exit_node
    
    def __str__(self):
        return f"IterationNode({self.node_id}): for {self.target_var} in enumerate({self.iterable})"

class ProgramGraph:
    """Program graph containing all nodes"""
    
    def __init__(self):
        self.nodes: List[ProgramNode] = []
        self.entry_node: Optional[ProgramNode] = None
        self.exit_nodes: List[ProgramNode] = []
    
    def add_node(self, node: ProgramNode):
        self.nodes.append(node)
        if self.entry_node is None:
            self.entry_node = node
    
    def add_exit_node(self, node: ProgramNode):
        if node not in self.exit_nodes:
            self.exit_nodes.append(node)
    
    def visualize(self):
        """Print the graph structure"""
        print("Program Graph:")
        print(f"Entry Node: {self.entry_node}")
        print("\nNodes:")
        for node in self.nodes:
            print(f"  {node}")
        print(f"\nExit Nodes: {[node.node_id for node in self.exit_nodes]}")

class CodeParser:
    """Parser for converting Python code to program graph"""
    
    def __init__(self, monitored_functions: Set[str]):
        self.monitored_functions = monitored_functions
        self.node_counter = 0
        self.graph = ProgramGraph()
        self.current_instructions = []
    
    def get_next_node_id(self) -> int:
        node_id = self.node_counter
        self.node_counter += 1
        return node_id
    
    def ast_to_string(self, node: ast.AST) -> str:
        """Convert AST node back to string representation"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Attribute):
            return f"{self.ast_to_string(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            func_name = self.ast_to_string(node.func)
            args = [self.ast_to_string(arg) for arg in node.args]
            kwargs = [f"{kw.arg}={self.ast_to_string(kw.value)}" for kw in node.keywords]
            all_args = ", ".join(args + kwargs)
            return f"{func_name}({all_args})"
        elif isinstance(node, ast.Compare):
            left = self.ast_to_string(node.left)
            comparisons = []
            for i, op in enumerate(node.ops):
                if isinstance(op, ast.Lt):
                    op_str = "<"
                elif isinstance(op, ast.Gt):
                    op_str = ">"
                elif isinstance(op, ast.Eq):
                    op_str = "=="
                elif isinstance(op, ast.LtE):
                    op_str = "<="
                elif isinstance(op, ast.GtE):
                    op_str = ">="
                elif isinstance(op, ast.NotEq):
                    op_str = "!="
                else:
                    op_str = str(type(op).__name__)
                right = self.ast_to_string(node.comparators[i])
                comparisons.append(f"{op_str} {right}")
            return left + " " + " ".join(comparisons)
        else:
            # Fallback for other node types
            try:
                import astor
                return astor.to_source(node).strip()
            except:
                return f"<{type(node).__name__}>"
    
    def flush_instructions(self) -> Optional[ProgramNode]:
        """Create an instruction node from accumulated instructions"""
        if self.current_instructions:
            node = InstructionNode(self.get_next_node_id(), self.current_instructions.copy())
            self.current_instructions.clear()
            return node
        return None
    
    def parse_function_call(self, call_node: ast.Call) -> MonitoredFunctionNode:
        """Parse a monitored function call"""
        func_name = self.ast_to_string(call_node.func)
        args = [self.ast_to_string(arg) for arg in call_node.args]
        kwargs = {kw.arg: self.ast_to_string(kw.value) for kw in call_node.keywords}
        
        return MonitoredFunctionNode(self.get_next_node_id(), func_name, args, kwargs)
    
    def is_monitored_function(self, node: ast.Call) -> bool:
        """Check if a function call should be monitored"""
        func_name = self.ast_to_string(node.func)
        return func_name in self.monitored_functions
    
    def parse_statements(self, statements: List[ast.stmt]) -> List[ProgramNode]:
        """Parse a list of statements into graph nodes"""
        nodes = []
        i = 0
        
        while i < len(statements):
            stmt = statements[i]
            
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                if self.is_monitored_function(stmt.value):
                    # Flush any accumulated instructions
                    if self.current_instructions:
                        nodes.append(self.flush_instructions())
                    
                    # Create monitored function node
                    nodes.append(self.parse_function_call(stmt.value))
                else:
                    self.current_instructions.append(self.ast_to_string(stmt))
            
            elif isinstance(stmt, ast.If):
                # Flush instructions before branch
                if self.current_instructions:
                    nodes.append(self.flush_instructions())
                
                # Create branch node
                condition = self.ast_to_string(stmt.test)
                branch_node = BranchNode(self.get_next_node_id(), condition)
                
                # Parse true branch
                true_branch_nodes = self.parse_statements(stmt.body)
                # true_branch = true_branch_nodes[0] if true_branch_nodes else None
                true_branch = ProgramScope(branch_node, true_branch_nodes)
                
                # Parse false branch (else)
                false_branch = None
                if stmt.orelse:
                    false_branch_nodes = self.parse_statements(stmt.orelse)
                    # false_branch = false_branch_nodes[0] if false_branch_nodes else None
                    false_branch = ProgramScope(branch_node, false_branch_nodes)
                
                if true_branch:
                    branch_node.set_branches(true_branch, false_branch)
                
                nodes.append(branch_node)
                nodes.extend(true_branch_nodes)
                if false_branch:
                    nodes.extend(false_branch_nodes)
            
            elif isinstance(stmt, ast.For):
                # Flush instructions before loop
                if self.current_instructions:
                    nodes.append(self.flush_instructions())
                
                # Check if it's an enumeration pattern
                target = self.ast_to_string(stmt.target)
                iterable = self.ast_to_string(stmt.iter)

                loop_node = IterationNode(self.get_next_node_id(), target, iterable)
                
                # Parse loop body
                body_nodes = self.parse_statements(stmt.body)
                # body = body_nodes[0] if body_nodes else None
                body = ProgramScope(loop_node, body_nodes)
                exit_nodes = self.parse_statements(stmt.orelse)
                exit_scope = ProgramScope(loop_node, exit_nodes)
                
                if body:
                    loop_node.set_body_and_exit(body, exit_scope)
                
                nodes.append(loop_node)
                nodes.extend(body_nodes)
            
            elif isinstance(stmt, ast.While):
                # Flush instructions before loop
                if self.current_instructions:
                    nodes.append(self.flush_instructions())
                
                condition = self.ast_to_string(stmt.test)
                loop_node = LoopNode(self.get_next_node_id(), 'while', "", condition)
                
                # Parse loop body
                body_nodes = self.parse_statements(stmt.body)
                # body = body_nodes[0] if body_nodes else None
                body = ProgramScope(loop_node, body_nodes)
                exit_nodes = self.parse_statements(stmt.orelse)
                exit_scope = ProgramScope(loop_node, exit_nodes)

                if body:
                    loop_node.set_body_and_exit(body, exit_nodes)
                
                nodes.append(loop_node)
                nodes.extend(body_nodes)
            
            else:
                # Regular instruction
                self.current_instructions.append(self.ast_to_string(stmt))
            
            i += 1
        
        # Flush any remaining instructions
        if self.current_instructions:
            nodes.append(self.flush_instructions())
        
        return [node for node in nodes if node is not None]
    
    def parse_code(self, code: str) -> ProgramGraph:
        """Parse Python code into a program graph"""
        tree = ast.parse(code)
        nodes = self.parse_statements(tree.body)
        
        # Add nodes to graph and connect them sequentially
        for i, node in enumerate(nodes):
            self.graph.add_node(node)
        
        return self.graph


import ast
from collections import defaultdict

def extract_variables_from_expr(expr_code: str):
    """
    Given a snippet of code, return sets of variable names:
    (defines, uses)
    """
    defines = set()
    uses = set()

    try:
        node = ast.parse(expr_code)
    except SyntaxError:
        return defines, uses

    class VarVisitor(ast.NodeVisitor):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Store):
                defines.add(n.id)
            elif isinstance(n.ctx, ast.Load):
                uses.add(n.id)
        def visit_FunctionDef(self, n):
            for arg in n.args.args:
                defines.add(arg.arg)
            self.generic_visit(n)
        def visit_Assign(self, n):
            for target in n.targets:
                self.visit(target)
            self.visit(n.value)
        def visit_AugAssign(self, n):
            self.visit(n.target)
            self.visit(n.value)
        def visit_For(self, n):
            self.visit(n.target)
            self.visit(n.iter)
            for stmt in n.body + n.orelse:
                self.visit(stmt)
        def visit_While(self, n):
            self.visit(n.test)
            for stmt in n.body + n.orelse:
                self.visit(stmt)

    VarVisitor().visit(node)
    return defines, uses

def get_node_def_use(node):
    """Return (defines, uses) for a ProgramNode."""
    defines = set()
    uses = set()

    if isinstance(node, InstructionNode):
        for instr in node.instructions:
            d, u = extract_variables_from_expr(instr)
            defines |= d
            uses |= u
    elif isinstance(node, MonitoredFunctionNode):
        # uses variables in args/kwargs
        for a in node.args:
            _, u = extract_variables_from_expr(a)
            uses |= u
        for v in node.kwargs.values():
            _, u = extract_variables_from_expr(v)
            uses |= u
    elif isinstance(node, BranchNode):
        _, u = extract_variables_from_expr(node.condition)
        uses |= u
    elif isinstance(node, LoopNode):
        _, u = extract_variables_from_expr(node.condition)
        uses |= u
        if node.initialization:
            d, u2 = extract_variables_from_expr(node.initialization)
            defines |= d
            uses |= u2
        if node.update:
            d, u2 = extract_variables_from_expr(node.update)
            defines |= d
            uses |= u2
    elif isinstance(node, IterationNode):
        # target_var is defined, iterable is used
        defines.add(node.target_var)
        _, u = extract_variables_from_expr(node.iterable)
        uses |= u

    return defines, uses

def build_data_flow_edges(graph: ProgramGraph):
    """
    Given a ProgramGraph, return list of (from_node_id, to_node_id)
    edges where `to` requires variables defined in `from`.
    """
    node_infos = {}
    for node in graph.nodes:
        node_infos[node.node_id] = get_node_def_use(node)

    edges = set()

    # brute-force compare each pair (A, B) with index(A) < index(B)
    for i, node_a in enumerate(graph.nodes):
        defs_a, _ = node_infos[node_a.node_id]
        for j, node_b in enumerate(graph.nodes):
            if j <= i:
                continue
            _, uses_b = node_infos[node_b.node_id]
            if defs_a & uses_b:
                edges.add((node_a.node_id, node_b.node_id))

    return sorted(edges)


# Example usage
def main():
    # Example Python code to parse
    code = """
x = 10
y = 20
monitored_func1(x, y)
z = x + y

if z > 25:
    monitored_func2(z, debug=True)
    print("Large sum")
else:
    print("Small sum")

for i, item in enumerate(['a', 'b', 'c']):
    process_item(item)
    monitored_func3(i, item)
else:
    print(1)

while x > 0:
    x = x - 1
    update_value(x)
"""
    
    # Define which functions to monitor
    monitored_functions = {'monitored_func1', 'monitored_func2', 'monitored_func3'}
    
    # Parse the code
    parser = CodeParser(monitored_functions)
    graph = parser.parse_code(code)

    # Build data flow edges
    edges = build_data_flow_edges(graph)

    # Print the edges
    for edge in edges:
        print(f"Edge from {edge[0]} to {edge[1]}")
    
    # Visualize the result
    graph.visualize()

if __name__ == "__main__":
    main()