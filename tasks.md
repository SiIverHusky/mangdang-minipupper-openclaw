# What to do

## Problems

* Barge-in is broken. Possible it is the VAD, but the TTS gets interrupted with something that is not user speech. Need to investigate and fix.
* Seems like OpenClaw does not keep memory of the conversation. Need to check if the gateway is storing conversation history and if the app is sending the right context with each request.
* The app assumes the user is talking about the gateway when asking the minipupper to do something. Need to ensure either OpenClaw or the app can detect when the user is giving a command to the minipupper vs. asking a question to the gateway.
* Need to set defaults of where actions are done, the node or the gateway. 