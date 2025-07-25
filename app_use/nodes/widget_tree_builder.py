import json
import logging
from dart_vm_client import DartVmServiceClient
from app_use.nodes.app_node import (
    AppElementNode,
    AppTextNode,
    NodeState,
    SelectorMap,
)

logger = logging.getLogger("WidgetTreeBuilder")

IRRELEVANT_TYPES = {
    'Focus', 'Actions', 'Semantics', 'Listener', 'MouseRegion', 'Builder',
    'GestureDetector', 'RawGestureDetector', 'CustomSingleChildLayout',
    'CustomMultiChildLayout', 'NotificationListener<LayoutChangedNotification>',
    
    'Padding', 'Align', 'Center', 'ConstrainedBox', 'DefaultTextStyle',
    'AnimatedDefaultTextStyle', 'IconTheme', 'IconButtonTheme', 'MediaQuery',
    'PhysicalModel', 'AnimatedPhysicalModel', 'ClipRect', 'ClipPath',
    'AnnotatedRegion<SystemUiOverlayStyle>', 'Expanded',
    'Transform', 'DecoratedBox', 'SizedBox', 'InkResponse',
    
    'RichText'
}

CANONICAL_NAMES = {
    'RichText': 'Text'
}

class WidgetTreeBuilder:
    """
    Builds a tree of AppNode objects from the Flutter widget tree using dart-vm-client.
    
    This class uses the DartVmServiceClient from dart-vm-client package to
    retrieve widget information from a running Flutter application and
    constructs a tree of AppNode objects representing the UI elements.
    """
    def __init__(self, service_client: DartVmServiceClient):
        self.service_client = service_client
        self._id_counter = 0

    def build_widget_tree(self, object_group="flutter"):
        """Build a tree of AppNode objects from the Flutter widget tree"""
        self._id_counter = 0
        
        try:
            response = self.service_client.get_root_widget_summary_tree_with_previews(object_group)
            
            if not hasattr(response, 'data') or not response.data:
                logger.warning("Response does not have data field or data is empty")
                return self._build_widget_tree_fallback(object_group)
            
            try:
                data = json.loads(response.data)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON: {e}")
                return self._build_widget_tree_fallback(object_group)
            
            if not data or 'result' not in data:
                logger.warning("Data missing 'result' field")
                return self._build_widget_tree_fallback(object_group)
            
            root_data = data['result']
            root_node = self._parse_widget_structure(root_data, None, object_group)
            
            all_nodes = self._collect_all_nodes(root_node)
            
            relevant_nodes = self._filter_relevant_nodes(all_nodes)
            
            self._setup_sibling_relationships(relevant_nodes)
            
            selector_map: SelectorMap = {node.unique_id: node for node in relevant_nodes}
            
            logger.info(
                "Built widget tree with %s total nodes, filtered to %s relevant nodes",
                len(all_nodes),
                len(relevant_nodes),
            )
            
            return NodeState(element_tree=root_node, selector_map=selector_map)
            
        except Exception as e:
                logger.error(f"Error building widget tree: {str(e)}")
        return self._build_widget_tree_fallback(object_group)

    def _build_widget_tree_fallback(self, object_group):
        """Fallback method to get widget tree using other methods"""
        logger.info("Using fallback method to build widget tree")
        
        try:
            # Get the root widget
            response = self.service_client.get_root_widget(object_group)
            
            if not hasattr(response, 'data') or not response.data:
                logger.warning("Root widget response has no data")
                return []
            
            try:
                data = json.loads(response.data)
            except json.JSONDecodeError:
                logger.error("Failed to parse root widget response as JSON")
                return []
            
            if 'result' not in data:
                logger.warning("Root response missing result field")
                return []
            
            root_result = data['result']
            
            if isinstance(root_result, str):
                details_response = self.service_client.get_details_subtree(
                    object_group, root_result, 100)
                
                if not hasattr(details_response, 'data') or not details_response.data:
                    return []
                
                try:
                    details_data = json.loads(details_response.data)
                    if 'result' not in details_data:
                        return []
                    
                    # Extract text preview if available
                    if 'textPreview' in details_data['result']:
                        text = details_data['result']['textPreview']
                    else:
                        text = None
                        
                    root_node = self._parse_widget_structure(details_data['result'], None, object_group)
                    all_nodes = self._collect_all_nodes(root_node)
                    
                    relevant_nodes = self._filter_relevant_nodes(all_nodes)
                    self._setup_sibling_relationships(relevant_nodes)
                    
                    selector_map: SelectorMap = {node.unique_id: node for node in relevant_nodes}
                    
                    logger.info(
                        "Built widget tree using fallback with %s total nodes, filtered to %s relevant nodes",
                        len(all_nodes),
                        len(relevant_nodes),
                    )
                    
                    return NodeState(element_tree=root_node, selector_map=selector_map)
                except Exception as e:
                    logger.error(f"Error parsing details tree: {str(e)}")
                    return []
                
            elif isinstance(root_result, dict):
                root_node = self._parse_widget_structure(root_result, None, object_group)
                all_nodes = self._collect_all_nodes(root_node)
                relevant_nodes = self._filter_relevant_nodes(all_nodes)
                self._setup_sibling_relationships(relevant_nodes)
                
                selector_map: SelectorMap = {node.unique_id: node for node in relevant_nodes}
                
                logger.info(
                    "Built widget tree using fallback with %s total nodes, filtered to %s relevant nodes",
                    len(all_nodes),
                    len(relevant_nodes),
                )
                
                return NodeState(element_tree=root_node, selector_map=selector_map)
            
            return []
        
        except Exception as e:
            logger.error(f"Error in fallback widget tree building: {str(e)}")
            return []

    def _parse_widget_structure(self, widget_data, parent, object_group):
        """Parse a widget from the Flutter Inspector response"""
        current_unique_id = self._id_counter
        self._id_counter += 1

        if not isinstance(widget_data, dict):
            return AppElementNode(
                unique_id=current_unique_id,
                node_type="Unknown",
                is_interactive=False,
                properties={},
                parent_node=parent
            )
        
        properties = {}
        for key, value in widget_data.items():
            if key != 'children':
                properties[key] = value
        
        node_type = widget_data.get('type', 'Unknown')
        description = widget_data.get('description', '')
        
        text = None

        if 'properties' in widget_data and isinstance(widget_data['properties'], list):
            for prop in widget_data['properties']:
                if isinstance(prop, dict):
                    prop_type = prop.get('type')
                    prop_name = prop.get('name')
                    if prop_type == 'StringProperty' and (prop_name == 'data' or prop_name == 'text'):
                        text_value = prop.get('value')
                        if text_value is not None:
                            text = text_value
                            break
        
        if text is None:
            text = widget_data.get('textPreview', None)
        
        key = None
        clean_node_type = node_type
        
        # Check if the description contains a key pattern like "WidgetType-[<'key_name'>]"
        if description and ('-[<\'' in description or '-[<"' in description):
            import re
            key_match = re.search(r"-\[<['\"](.+?)['\"]\>\]", description)
            if key_match:
                key = key_match.group(1)
                if 'widgetRuntimeType' in widget_data:
                    clean_node_type = widget_data['widgetRuntimeType']
                else:
                    clean_type_match = re.match(r"^([^\-\[]+)", description)
                    if clean_type_match:
                        clean_node_type = clean_type_match.group(1).strip()
        
        is_interactive = self._is_widget_interactive(properties)
        
        if 'widgetRuntimeType' in widget_data:
            clean_node_type = widget_data['widgetRuntimeType']
        
        clean_node_type = CANONICAL_NAMES.get(clean_node_type, clean_node_type)
        
        is_relevant = (not clean_node_type.startswith('_') and \
                      not clean_node_type in IRRELEVANT_TYPES) or \
                     text is not None or \
                     key is not None or \
                     properties.get('createdByLocalProject', False)
                     
        if not is_relevant:
            # Process children to avoid losing important descendants
            children_nodes = []
            if 'children' in widget_data and isinstance(widget_data['children'], list):
                for child_data in widget_data['children']:
                    child_node = self._parse_widget_structure(child_data, parent, object_group)
                    if child_node is not None:  # Only add non-None nodes
                        children_nodes.append(child_node)
            
            if not children_nodes:
                return None
            
            if len(children_nodes) == 1:
                return children_nodes[0]
            
            return children_nodes[0]
        
        if clean_node_type == "Text" and text is not None and not widget_data.get("children"):
            node: AppElementNode | AppTextNode = AppTextNode(
                unique_id=current_unique_id,
                text=text,
                parent=parent,
            )
        else:
            node = AppElementNode(
                unique_id=current_unique_id, # Use sequential ID
                node_type=clean_node_type,
                is_interactive=is_interactive,
                properties=properties,
                parent_node=parent,
                text=text,
                key=key
            )
        
        # Process children if they exist
        if 'children' in widget_data and isinstance(widget_data['children'], list):
            for child_data in widget_data['children']:
                child_node = self._parse_widget_structure(child_data, node, object_group)
                if child_node is not None:  # Only add non-None nodes
                    node.child_nodes.append(child_node)
        
        current_node_value_id = widget_data.get('valueId')
        container_keywords = [
            'bottomnavigationbar', 'container', 'card', 'appbar', 'listview', 'gridview', 'listtile'
        ]

        if current_node_value_id and \
           any(keyword in node.node_type.lower() for keyword in container_keywords):
            try:
                logger.info(f"Node {node.node_type} (ID: {current_node_value_id}) is a container. Fetching detailed children.")
                children_details_response = self.service_client.get_children_details_subtree(
                    object_group,
                    current_node_value_id
                )

                if children_details_response and hasattr(children_details_response, 'data') and children_details_response.data:
                    children_data_json = json.loads(children_details_response.data)
                    
                    children_list_to_parse = []
                    if 'result' in children_data_json:
                        if isinstance(children_data_json['result'], dict) and 'children' in children_data_json['result']:
                            children_list_to_parse = children_data_json['result']['children']
                        elif isinstance(children_data_json['result'], list):
                            children_list_to_parse = children_data_json['result']
                    elif isinstance(children_data_json, dict) and 'children' in children_data_json: # Fallback if result is the node itself
                         children_list_to_parse = children_data_json['children']
                        
                    if children_list_to_parse:
                        fetched_children_count = len(children_list_to_parse)
                        logger.info(f"Found {fetched_children_count} detailed children for {node.node_type} (ID: {current_node_value_id}). Replacing existing summary children if any.")
                        node.child_nodes.clear()
                        for child_detail_data in children_list_to_parse:
                            child_node = self._parse_widget_structure(child_detail_data, node, object_group)
                            node.child_nodes.append(child_node)
                    else:
                        logger.info(f"get_children_details_subtree for {current_node_value_id} returned no parseable children list or it was empty.")
                else:
                    logger.warning(f"No data in get_children_details_subtree response for {current_node_value_id}.")

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from get_children_details_subtree for {current_node_value_id}: {e}")
            except Exception as e:
                logger.error(f"Error fetching/processing children details for {current_node_value_id}: {str(e)}")
        
        return node
    
    def _is_widget_interactive(self, properties):
        """Determine if a widget is interactive based on its properties"""
        node_type = ''
        if 'type' in properties:
            node_type = properties['type']
        if 'widgetRuntimeType' in properties:
            node_type = properties['widgetRuntimeType']
        
        description = properties.get('description', '')
        if any(keyword in description for keyword in ['Search', 'Button', 'Card', 'Input', 'Field', 'Select', 'Click', 'Tap',
            'Toggle', 'Slide', 'Scroll', 'Dropdown', 'Menu', 'Navigation', 'Home',
            'Profile', 'Category', 'Product', 'Item', 'Bar', 'Icon', 'Tab', 'List',
            'Tile', 'Link'
        ]):
            return True
        
        
        interactive_widgets = [
    # Buttons
    'Button', 'TextButton', 'ElevatedButton', 'OutlinedButton', 'IconButton',
    'FloatingActionButton', 'CupertinoButton', 'BackButton', 'CloseButton',
    'FilledButton',
    
    # Input fields
    'TextField', 'TextFormField', 'CupertinoTextField', 'CupertinoSearchField',
    
    # Selection widgets
    'Checkbox', 'Radio', 'Switch', 'Slider', 'RangeSlider', 'DropdownButton',
    'PopupMenuButton', 'ToggleButtons', 'CupertinoSwitch', 'CupertinoPicker',
    'MenuAnchor', 'DropdownMenu',
    
    # Interactive containers
    'GestureDetector', 'InkWell', 'InkResponse', 'Draggable', 'LongPressDraggable',
    'DragTarget', 'Dismissible', 'ExpansionPanel', 'DraggableScrollableSheet',
    'Carousel', 'InteractiveViewer',
    
    # Navigation
    'TabBar', 'BottomNavigationBar', 'NavigationBar', 'NavigationRail', 'Drawer',
    'CupertinoTabBar', 'CupertinoNavigationBar',
    
    # Cards and tiles
    'ListTile', 'CheckboxListTile', 'RadioListTile', 'SwitchListTile', 'ExpansionTile',
    'Card', 'ActionChip', 'FilterChip', 'ChoiceChip', 'InputChip',
    
    # Scrollable
    'Scrollable', 'ScrollView', 'ListView', 'GridView', 'PageView',
    'CustomScrollView', 'SingleChildScrollView', 'ReorderableListView',
    'Scrollbar',
    
    # Search and app bars
    'SearchBar', 'AppBar', 'SliverAppBar', 'CupertinoSearchTextField',
    'SearchAnchor',
    
    # Form elements
    'Form', 'FormField',
    
    # Date and time pickers
    'DatePicker', 'TimePicker', 'CupertinoDatePicker', 'CalendarDatePicker',
    
    # Text selection
    'SelectableText',
    
    # Custom UI components that might be interactive
    'BottomSheet', 'Dialog', 'AlertDialog', 'SimpleDialog', 'CupertinoAlertDialog',
    'Scaffold', 'SnackBar', 'RefreshIndicator', 'Tooltip'
]
        
        if any(interactive in node_type for interactive in interactive_widgets):
            return True
        
        if any(prop in properties for prop in ['gestures', 'onTap', 'onPressed', 'onChanged', 'onSubmitted']):
            return True
        
        if properties.get('stateful') == True:
            return True
        
        if properties.get('createdByLocalProject') == True:
            return True
        
        interactive_terms = [
            'Search', 'Button', 'Card', 'Input', 'Field', 'Select', 'Click', 'Tap',
            'Toggle', 'Slide', 'Scroll', 'Dropdown', 'Menu', 'Navigation', 'Home',
            'Profile', 'Category', 'Product', 'Item', 'Bar', 'Icon', 'Tab', 'List',
            'Tile', 'Link'
        ]
        
        if description and any(term.lower() in description.lower() for term in interactive_terms):
            return True
        
        return False

    def _collect_all_nodes(self, root):
        """Recursively collect all nodes in a flat list"""
        if not root:
            return []
            
        result = [root]
        for child in root.child_nodes:
            result.extend(self._collect_all_nodes(child))
        return result

    def _setup_sibling_relationships(self, all_nodes):
        """Set up previous/next sibling relationships"""
        nodes_by_parent = {}
        for node in all_nodes:
            parent = node.parent_node
            if parent not in nodes_by_parent:
                nodes_by_parent[parent] = []
            nodes_by_parent[parent].append(node)
        
        for parent, siblings in nodes_by_parent.items():
            for i in range(len(siblings)):
                if i > 0:
                    siblings[i].previous_sibling = siblings[i-1]
                if i < len(siblings) - 1:
                    siblings[i].next_sibling = siblings[i+1]
    
    def _filter_relevant_nodes(self, nodes):
        """Filter the widget nodes to only include relevant ones"""
        if not nodes:
            return []
        
        result = []
        for node in nodes:
                
            if hasattr(node, 'node_type'):
                if node.node_type.startswith('_'):
                    continue
                    
                if node.node_type in IRRELEVANT_TYPES and not (
                    node.text or 
                    node.key or 
                    node.is_interactive or 
                    node.properties.get('createdByLocalProject', False)
                ):
                    continue
                    
                if node.node_type in CANONICAL_NAMES:
                    node.node_type = CANONICAL_NAMES[node.node_type]
                
                result.append(node)
        
        logger.info(f"Filtered widget tree from {len(nodes)} to {len(result)} relevant nodes")
        return result

    def dispose(self, object_group="flutter"):
        """Clean up resources"""
        try:
            self.service_client.dispose_all_groups(object_group)
        except Exception as e:
            logger.error(f"Error disposing widget tree resources: {str(e)}")