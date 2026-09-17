"""Central runtime switches. Voice code is retained, opt-in only."""
import os
VOICE_ENABLED = os.environ.get('KIRA_VOICE_ENABLED', 'false').lower() == 'true'
