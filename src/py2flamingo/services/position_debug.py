"""Debug/diagnostic commands for the Connection tab and the Pixel Calibrator.

Contains PositionDebugHelper (reached through
``PositionController.debug_query_command`` / ``debug_save_settings``), used by
the Connection tab's debug buttons and
by the Pixel Calibrator's "push magnification to the scope". Every command
goes through the command service (``MicroscopeCommandService``): the one
locked writer, and replies matched by the background reader. Nothing here
reads the command socket itself.
"""

import logging
from typing import Optional

from py2flamingo.core import server_layout
from py2flamingo.core.command_codes import StageCommands
from py2flamingo.core.frames import END_MARKER, START_MARKER

logger = logging.getLogger(__name__)

#: How long a debug query or a settings save waits for its reply.
DEBUG_REPLY_TIMEOUT_S = 3.0
SETTINGS_SAVE_REPLY_TIMEOUT_S = 5.0


class PositionDebugHelper:
    """Helper class for debug/diagnostic commands on position hardware.

    Sends a command and shows its reply, and sends a settings file back to
    the scope, for the connection debug view.
    """

    # Command codes
    COMMAND_CODES_STAGE_POSITION_GET = StageCommands.POSITION_GET

    # Frame markers bracketing every 128-byte command/response
    # (core/frames.py). A response is only trustworthy when BOTH
    # are right: a good start with a bad end means a truncated or misaligned
    # frame, and the status/command fields between them are then meaningless.
    START_MARKER = START_MARKER
    END_MARKER = END_MARKER

    def __init__(self, connection_service):
        """Initialize the debug helper.

        Args:
            connection_service: MVCConnectionService for microscope communication
        """
        self.connection = connection_service
        self.logger = logging.getLogger(__name__)
        self._service = None

    def _commands(self):
        """The command service these sends go through (built on first use)."""
        if self._service is None:
            from py2flamingo.services.microscope_command_service import (
                MicroscopeCommandService,
            )

            self._service = MicroscopeCommandService(self.connection)
        return self._service

    def _exchange(
        self,
        code: int,
        params,
        payload: bytes = b"",
        reply_codes=None,
        timeout: float = DEBUG_REPLY_TIMEOUT_S,
    ) -> Optional[dict]:
        """Send one frame (and ``payload`` after it) and wait for the reply.

        The reply is matched by the background reader (any of
        ``reply_codes``, default the same code). Returns the reply as
        ``frame_fields`` dict plus ``raw`` (the 128 bytes), or None on
        timeout (``MicroscopeCommandService.exchange_frame``).
        """
        packet = self.connection.encoder.encode_command(
            code=code,
            status=0,
            params=params,
            value=0.0,
            data=b"",
            additional_data_size=len(payload),
        )
        return self._commands().exchange_frame(
            packet + payload, reply_codes or code, timeout=timeout
        )

    def debug_query_command(self, command_code: int, command_name: str) -> dict:
        """Send a command and return parsed response for debugging.

        The reply is matched by the background reader like every other one.

        Args:
            command_code: The command code to send
            command_name: Human-readable name for logging/display

        Returns:
            Dictionary containing parsed response:
                - 'success': bool - Whether query succeeded
                - 'command_code': int - Command code sent
                - 'command_name': str - Command name
                - 'raw_response': bytes - The 128-byte reply
                - 'parsed': dict - Parsed response structure
                - 'error': str - Error message if failed
                - 'timeout_explanation': str - Explanation if timeout
        """
        from py2flamingo.core.tcp_protocol import CommandDataBits

        if not self.connection.is_connected():
            return {
                "success": False,
                "command_code": command_code,
                "command_name": command_name,
                "error": "Not connected to microscope",
            }

        try:
            self.logger.info(f"Sending {command_name} (code {command_code})...")

            # The callback bit makes the server answer. For STAGE_POSITION_GET
            # int32Data0 must name an axis (1=X, 2=Y, 3=Z, 4=R).
            params = [0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]
            if command_code == self.COMMAND_CODES_STAGE_POSITION_GET:
                params[3] = 1  # X axis
                self.logger.info(
                    "STAGE_POSITION_GET: Setting params[3] (int32Data0) = 1 for X-axis"
                )

            reply = self._exchange(command_code, params)
            if reply is None:
                return self._timeout_result(command_code, command_name)

            response_code = reply["command_code"]
            status_code = reply["status_code"]
            value = reply["value"]
            add_data_bytes = reply["reserved"]
            additional_data = reply["additional_data"]
            data_tail_str = (
                reply["data"].rstrip(b"\x00").decode("utf-8", errors="replace")
            )
            additional_data_str = (
                additional_data.rstrip(b"\x00").decode("utf-8", errors="replace")
                if additional_data
                else ""
            )

            self.logger.info(
                f"Parsed response: code={response_code}, status={status_code}, "
                f"value={value}, addDataBytes={add_data_bytes}"
            )

            parsed = {
                "response_type": "Binary Protocol",
                "start_marker": f"0x{reply['start_marker']:08X}",
                "command_code": response_code,
                "status_code": status_code,
                "params": reply["params"],
                "value": value,
                "reserved": add_data_bytes,
                "data_tail_string": data_tail_str,
                "additional_data": additional_data,  # Raw bytes
                "additional_data_string": additional_data_str,  # Decoded string
                "full_data": f"Binary protocol response",
                "data_length": 128 + len(additional_data),
            }

            return {
                "success": True,
                "command_code": command_code,
                "command_name": command_name,
                "raw_response": reply["raw"],
                "parsed": parsed,
                "interpretation": self._interpret_command_response(
                    parsed, command_code, command_name
                ),
            }

        except (TimeoutError, OSError) as e:
            if isinstance(e, TimeoutError) or "timed out" in str(e):
                return self._timeout_result(command_code, command_name)
            self.logger.error(f"Failed to query {command_name}: {e}", exc_info=True)
            return {
                "success": False,
                "command_code": command_code,
                "command_name": command_name,
                "error": f"Communication error: {str(e)}",
            }
        except Exception as e:
            self.logger.error(f"Failed to query {command_name}: {e}", exc_info=True)
            return {
                "success": False,
                "command_code": command_code,
                "command_name": command_name,
                "error": f"Communication error: {str(e)}",
            }

    def _timeout_result(self, command_code: int, command_name: str) -> dict:
        self.logger.error(f"Timeout waiting for response from {command_name}")
        return {
            "success": False,
            "command_code": command_code,
            "command_name": command_name,
            "error": "timeout",
            "timeout_explanation": (
                f"No response from microscope after sending {command_name} (code {command_code}).\n\n"
                "This likely means:\n"
                "1. Command is NOT IMPLEMENTED in microscope firmware\n"
                "2. Command is defined in CommandCodes.h but never used\n"
                "3. Microscope ignores unknown/unimplemented commands\n\n"
                "Try other commands to see which ones are actually implemented."
            ),
        }

    def _interpret_command_response(
        self, parsed: dict, command_code: int, command_name: str
    ) -> str:
        """Interpret what a command response contains.

        Args:
            parsed: Parsed response dictionary
            command_code: Command code that was sent
            command_name: Human-readable command name

        Returns:
            Human-readable interpretation string
        """
        interpretation_lines = []

        interpretation_lines.append("RESPONSE ANALYSIS:")
        interpretation_lines.append(
            f"  Command sent: {command_name} (code {command_code})"
        )

        # Check if we have full data
        full_data = parsed.get("full_data", "")
        data_length = parsed.get("data_length", 0)

        if full_data and data_length > 0 and not full_data.startswith("<"):
            interpretation_lines.append(
                f"\n  ✓ Received text response: {data_length} characters"
            )
            interpretation_lines.append(f"  Data preview: {repr(full_data[:100])}")

            # Check if this looks like settings data
            if (
                "<Type>" in full_data
                or "Filter wheel" in full_data
                or "Stage limits" in full_data
            ):
                interpretation_lines.append("\n  ⚠ UNEXPECTED BEHAVIOR DETECTED:")
                interpretation_lines.append(
                    "  ⚠ Command STAGE_POSITION_GET returned SETTINGS data!"
                )
                interpretation_lines.append(
                    f"  ⚠ This is the same data returned by SCOPE_SETTINGS_LOAD (code 4105)"
                )
                interpretation_lines.append("\n  This response contains:")
                if "Filter wheel" in full_data:
                    interpretation_lines.append("    - Filter wheel configuration")
                if "Stage limits" in full_data:
                    interpretation_lines.append("    - Stage limits and home position")
                if "<Type>" in full_data:
                    interpretation_lines.append("    - Microscope type and name")
                if "LED settings" in full_data:
                    interpretation_lines.append("    - LED settings")
                interpretation_lines.append("\n  But it does NOT contain:")
                interpretation_lines.append(
                    "    - Current X, Y, Z, R position coordinates"
                )
                interpretation_lines.append("    - Any real-time position feedback")
        else:
            interpretation_lines.append(
                f"\n  Response type: {parsed.get('response_type', 'Unknown')}"
            )
            interpretation_lines.append(f"  Data available: {data_length} characters")

        interpretation_lines.append("\n  CONCLUSION:")

        # Provide specific conclusions based on command type
        if command_code == 40967:  # SYSTEM_STATE_GET
            status_code = parsed.get("status_code", 0)
            params = parsed.get("params", [])

            interpretation_lines.append(
                f"  ✓ SYSTEM_STATE_GET is IMPLEMENTED and working!"
            )
            interpretation_lines.append(f"\n  System State Interpretation:")
            interpretation_lines.append(f"    Status Code: {status_code}")

            if status_code == 1:
                interpretation_lines.append(
                    f"    → System is IDLE (ready for commands)"
                )
            elif status_code == 0:
                interpretation_lines.append(f"    → System is BUSY (executing command)")
            else:
                interpretation_lines.append(f"    → Unknown status: {status_code}")

            if len(params) > 3 and params[3] != 0:
                state_code = params[3]
                interpretation_lines.append(f"\n    State Code: {state_code}")
                if state_code == 40962:
                    interpretation_lines.append(f"    → SYSTEM_STATE_IDLE (40962)")
                else:
                    interpretation_lines.append(f"    → Unknown state code")

            interpretation_lines.append(
                "\n  This command successfully queries system state!"
            )
            interpretation_lines.append(
                "  Use this to check if microscope is ready for commands."
            )

        elif command_code == 12327:  # CAMERA_IMAGE_SIZE_GET
            params = parsed.get("params", [])
            interpretation_lines.append(f"  ✓ CAMERA_IMAGE_SIZE_GET query")
            interpretation_lines.append(f"\n  Camera Image Size:")
            if len(params) > 0 and params[0] != 0:
                # Old code used received[7] which would be params[4]
                # But let's check all params for non-zero values
                interpretation_lines.append(f"    Parameters: {params}")
                interpretation_lines.append(
                    f"    → Image size info returned in parameters"
                )
            else:
                interpretation_lines.append(f"    Parameters: {params}")
                interpretation_lines.append(
                    f"    Note: Check which parameter field contains image size"
                )

        elif command_code == 12343:  # CAMERA_PIXEL_FIELD_OF_VIEW_GET
            value = parsed.get("value", 0.0)
            interpretation_lines.append(f"  ✓ CAMERA_PIXEL_FIELD_OF_VIEW_GET query")
            interpretation_lines.append(f"\n  Pixel Field of View:")
            interpretation_lines.append(f"    Value: {value}")
            if value > 0:
                interpretation_lines.append(
                    f"    → Pixel FOV = {value} (likely in micrometers)"
                )
            else:
                interpretation_lines.append(
                    f"    Note: Value is zero - check if command is implemented"
                )

        elif command_code == 12293:  # CAMERA_WORK_FLOW_STOP
            status_code = parsed.get("status_code", 0)
            interpretation_lines.append(f"  ✓ CAMERA_WORK_FLOW_STOP command")
            interpretation_lines.append(f"\n  Workflow Stop:")
            interpretation_lines.append(f"    Status: {status_code}")
            interpretation_lines.append(
                f"    → Command sent to stop any running workflow"
            )
            interpretation_lines.append(
                f"    → Safe to test (stops acquisition if running)"
            )

        elif command_code == 24592:  # STAGE_MOTION_STOPPED
            interpretation_lines.append(f"  Testing STAGE_MOTION_STOPPED command...")
            interpretation_lines.append(
                "  This should indicate if stage has finished moving."
            )

        elif command_code == 4103:  # COMMON_SCOPE_SETTINGS
            interpretation_lines.append(f"  Testing COMMON_SCOPE_SETTINGS command...")
            interpretation_lines.append(
                "  Different from _LOAD, might query without writing file."
            )

        elif command_code == self.COMMAND_CODES_STAGE_POSITION_GET:
            interpretation_lines.append(
                "  STAGE_POSITION_GET does NOT return current stage position."
            )
            interpretation_lines.append(
                "  Instead, it returns microscope configuration settings."
            )
            interpretation_lines.append("\n  Without position feedback:")
            interpretation_lines.append(
                "  - Software must track position locally (can drift)"
            )
            interpretation_lines.append("  - Cannot detect manual stage movement")
            interpretation_lines.append(
                "  - Cannot verify movements completed successfully"
            )
        elif full_data and data_length > 0:
            interpretation_lines.append(
                f"  {command_name} returned {data_length} characters of data."
            )
            if "<Type>" in full_data or "Filter wheel" in full_data:
                interpretation_lines.append(
                    "  Response contains microscope configuration/settings data."
                )
            else:
                interpretation_lines.append(
                    "  Response type unclear - review full data above."
                )
        else:
            interpretation_lines.append(
                f"  {command_name} returned limited or no data."
            )
            interpretation_lines.append(
                "  Check if command is implemented and what it should return."
            )

        return "\n".join(interpretation_lines)

    def debug_save_settings(self, settings_data: bytes) -> dict:
        """Send a settings file to the microscope with SCOPE_SETTINGS_SAVE.

        3.0.0 servers only (the 2026-10-06 builds moved the settings into the
        metadata document and dropped the command; there it is refused before
        anything is sent). The file goes as additional data after the frame,
        in one locked write. The server answers (ControlServer::
        saveScopeSettings):

        * COMMON_SCOPE_SETTINGS (0x1007), status 1, echoing the file: it took
          the settings (held in memory: this client does not set the
          SAVE_TO_DISK bit, so the scope's own uC copy is not rewritten);
        * MESSAGE_FROM_CONTROLLER "Save scope settings error - invalid scope
          settings file": it refused them;
        * nothing, for an empty file.

        It never answers with SCOPE_SETTINGS_SAVE itself, so the ack this used
        to wait for (code 0x1008, status 0) could not arrive from a real
        server; and it read that ack on the raw socket, racing the reader.

        Args:
            settings_data: Settings file content as bytes

        Returns:
            Dictionary with success status and message
        """
        if not self.connection.is_connected():
            return {"success": False, "error": "Not connected to microscope"}

        try:
            try:
                save_code = server_layout.code(
                    "COMMON_SCOPE_SETTINGS_SAVE", fallback=0x1008
                )
                echo_code = server_layout.code("COMMON_SCOPE_SETTINGS", fallback=0x1007)
                message_code = server_layout.code(
                    "COMMON_MESSAGE_FROM_CONTROLLER", fallback=0x1006
                )
            except server_layout.CommandUnavailable as e:
                return {
                    "success": False,
                    "error": (
                        "This server build has no SCOPE_SETTINGS_SAVE: its "
                        f"settings live in the metadata document ({e})"
                    ),
                }

            self.logger.info(
                f"Sending SCOPE_SETTINGS_SAVE with {len(settings_data)} bytes of data"
            )
            reply = self._exchange(
                save_code,
                [0, 0, 0, 0, 0, 0, 0],
                payload=settings_data,
                reply_codes=(echo_code, message_code),
                timeout=SETTINGS_SAVE_REPLY_TIMEOUT_S,
            )
            if reply is None:
                return {
                    "success": False,
                    "error": "Timeout waiting for acknowledgment (command may not be implemented)",
                }
            return self._judge_save_reply(reply, echo_code, message_code)

        except Exception as e:
            self.logger.error(f"Failed to save settings: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _judge_save_reply(self, reply: dict, echo_code: int, message_code: int) -> dict:
        """Success only for the settings echo with status 1 in a whole frame.

        This call replaces the settings the server works with, so "it
        replied" is not the same as "it worked".
        """
        start_marker = reply["start_marker"]
        response_code = reply["command_code"]
        status_code = reply["status_code"]
        end_marker = reply["end_marker"]
        detail = (
            f"marker=0x{start_marker:08X}, code={response_code}, "
            f"status={status_code}, end=0x{end_marker:08X}"
        )
        self.logger.info(f"Acknowledgment: {detail}")

        if start_marker != self.START_MARKER:
            return {
                "success": False,
                "error": f"Invalid response start marker: {detail}",
            }
        if end_marker != self.END_MARKER:
            # A good start marker with a bad end marker means the frame is
            # truncated or misaligned: the fields between cannot be trusted.
            return {
                "success": False,
                "error": f"Malformed response frame (bad end marker): {detail}",
            }
        if response_code == message_code:
            text = (
                (reply.get("additional_data") or b"")
                .decode("utf-8", "replace")
                .replace("\x00", "")
                .strip()
            )
            return {
                "success": False,
                "error": f"Scope refused the settings: {text or '(no text)'}: {detail}",
            }
        if response_code != echo_code:
            return {
                "success": False,
                "error": (
                    f"Scope answered a different command ({response_code}, "
                    f"expected {echo_code}): {detail}"
                ),
            }
        if status_code != 1:
            return {
                "success": False,
                "error": (
                    f"Scope reported status {status_code} (1 = settings taken). "
                    f"Settings may NOT have been saved: {detail}"
                ),
            }
        return {
            "success": True,
            "message": (
                f"Microscope acknowledged settings save.\n"
                f"Status code: {status_code}\n"
                f"Response code: {response_code}"
            ),
        }
