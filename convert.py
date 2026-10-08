#!/usr/bin/env python3
"""
Convert BASIC without line numbers (but with labels) to BASIC with line numbers.
Just so we don't need to get lost in line numbers.
It will also strip REM comments to save space, but use REM to name the label 
positions to maintain some minimum readbility.

Usage:
    ./convert.py [-v|--verbose] [-c|--comments] input.bas output.bas

Use -v or --verbose to print the labels found during conversion.
Use -c or --comments to preserve REM comments in the output.

By default, REM lines are filtered out and not included in the output.

Labels are defined as IDENTIFIER: on their own line.
Labels are converted to REM comments with line numbers.
GOTO/GOSUB/ON GOTO/ON GOSUB references to labels are converted to line numbers.

Example input:
    MOVE:
    INPUT D$
    IF D$="N" THEN GOTO END
    GOTO MOVE
    END:
    PRINT "END OF PROGRAM"

Example output:
    2100 REM MOVE
    2110 IF D$="N" THEN GOTO 2160
    2150 GOTO 2100
    2160 REM END
    2170 PRINT "END OF PROGRAM"
"""

import argparse
import re
import sys


def is_valid_label(label):
    """Check if the label is a valid BASIC identifier."""
    # BASIC identifiers: start with letter, followed by letters, digits, underscores
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9_]*$', label))


def filter_rem_lines(lines):
    """Filter out lines that are REM comments (case-insensitive)."""
    filtered = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith('REM '):
            continue
        filtered.append(line)
    return filtered


def parse_labels_and_lines(lines):
    """
    Parse input lines and identify labels and their line numbers.
    Returns a tuple of (label_map, processed_lines).
    label_map: dict mapping label name to line number
    processed_lines: list of (line_number, statement) tuples
    """
    label_map = {}
    processed_lines = []
    current_line_num = 100  # Start at 100
    line_increment = 10     # Increment by 10
    
    # First pass: identify all labels and assign line numbers
    label_lines = []  # List of (label_name, current_line_num) tuples
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Check if line is a label (IDENTIFIER:)
        if re.match(r'^[A-Za-z][A-Za-z0-9_]*:$', stripped):
            label_name = stripped[:-1]  # Remove the trailing colon
            if is_valid_label(label_name):
                label_lines.append((label_name, current_line_num))
                current_line_num += line_increment
        else:
            # Regular line
            current_line_num += line_increment
    
    # Build label map with all labels
    for label_name, line_num in label_lines:
        label_map[label_name] = line_num
    
    # Second pass: process lines with full label map
    current_line_num = 100  # Reset
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # Check if this line is a label
        if re.match(r'^[A-Za-z][A-Za-z0-9_]*:$', stripped):
            label_name = stripped[:-1]  # Remove the trailing colon
            if is_valid_label(label_name):
                line_num = label_map[label_name]
                # Add REM comment with the label
                processed_lines.append((line_num, f"REM {label_name}"))
                current_line_num = line_num + line_increment
        else:
            # Process regular line - check for label references
            statement = stripped
            statement = convert_labels_to_line_numbers(statement, label_map)
            processed_lines.append((current_line_num, statement))
            current_line_num += line_increment
    
    return label_map, processed_lines


def convert_labels_to_line_numbers(statement, label_map):
    """
    Replace label references in a statement with actual line numbers.
    Handles: GOTO label, GOSUB label, ON expr GOTO label, ON expr GOSUB label
    Also handles: IF ... THEN label
    """
    # Process labels in reverse order of length to avoid partial matches
    sorted_labels = sorted(label_map.items(), key=lambda x: len(x[0]), reverse=True)
    
    for label_name, line_num in sorted_labels:
        line_num_str = str(line_num)
        
        # Pattern to match label with proper delimiters
        # Label can be preceded by: start, space, comma, colon, or THEN
        # Label can be followed by: comma, space, colon, or end
        # Using capture groups to preserve delimiters
        pattern = r'([,\s:]|^|THEN\s+)(' + re.escape(label_name) + r')([,\s:]|$)'
        def replacer(m):
            return m.group(1) + line_num_str + m.group(3)
        
        statement = re.sub(pattern, replacer, statement, flags=re.IGNORECASE)
    
    return statement


def format_output(processed_lines):
    """Format processed lines as output string."""
    lines = []
    for line_num, statement in processed_lines:
        lines.append(f"{line_num} {statement}")
    return '\n'.join(lines)


def has_line_numbers(lines):
    """Check if the file already has line numbers."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Check if line starts with a number followed by a space or tab
        if re.match(r'^\d+\s', stripped):
            return True
    return False

def convert_file(input_path, output_path, verbose=False, comments=False):
    """Convert a BASIC file from labels to line numbers."""
    with open(input_path, 'r') as f:
        lines = f.readlines()
    
    if has_line_numbers(lines):
        raise ValueError("Input file already contains line numbers. This converter is for files without line numbers.")
    
    # Filter out REM lines unless --comments is specified
    if not comments:
        lines = filter_rem_lines(lines)
    
    label_map, processed_lines = parse_labels_and_lines(lines)
    output = format_output(processed_lines)
    
    with open(output_path, 'w') as f:
        f.write(output + '\n')
    
    print(f"Converted {len(processed_lines)} lines")
    if verbose:
        print(f"Found {len(label_map)} labels: {list(label_map.keys())}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the labels found during conversion")
    parser.add_argument("-c", "--comments", action="store_true",
                        help="preserve REM comments (don't filter them)")
    parser.add_argument("input_file", help="input BASIC file")
    parser.add_argument("output_file", help="output BASIC file")
    args = parser.parse_args()
    
    try:
        convert_file(args.input_file, args.output_file, args.verbose, args.comments)
    except FileNotFoundError:
        print(f"Error: Input file '{args.input_file}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()