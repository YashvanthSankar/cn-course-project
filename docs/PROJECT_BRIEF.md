# CN Lab Project: LAN-Based Multi-User Collaborative Whiteboard

> Markdown transcription of the official project brief (`docs/brief/CN_Lab_Project_2026_Whiteboard_Spec.pdf`).
> The PDF is the authoritative version.

**Submission deadline:** 15th November 2026, 11:55 pm IST. No further extensions will be given.

## Problem Statement
The goal of this project is to develop a robust, standalone, server-based multi-user collaborative
whiteboard application that operates exclusively over a Local Area Network (LAN). The system will
enable multiple users to collaborate in real time on a shared digital canvas, allowing them to draw,
write, annotate, and modify content simultaneously. The application must ensure that changes made by
one user are propagated to all other connected users while maintaining a consistent shared workspace.
It should function without internet connectivity and provide a reliable platform for collaborative
learning, brainstorming, technical discussions, and group problem-solving.

## Core Functional Requirements
The application must be developed using socket programming to manage network communication between
a central server and multiple clients. It must implement the following core modules:

### 1. Multi-User Whiteboard and Real-Time Drawing
- **Drawing and Annotation:** Users must be able to draw freehand strokes, erase content, and select colour on a shared canvas.
- **Real-Time Synchronization:** Drawing operations performed by any user must be transmitted to the server and propagated to all other connected clients with minimal delay.
- **Server-Side Coordination:** The server must receive, validate, and distribute drawing events to the appropriate clients while managing concurrent operations from multiple users.
- **Shared Canvas:** Every participant must see a consistent representation of the whiteboard, including updates made by other users during the session.
- **Canvas Reset and Persistence:** Users with appropriate permissions must be able to clear the canvas. The application must support saving the current whiteboard state and restoring it when required.

### 2. Multi-User Session and Workspace Management
- **Session Creation and Joining:** A user must be able to host a whiteboard session, and other users must be able to join using the server's LAN address and the appropriate session identifier or room code.
- **Participant Management:** The server must maintain a list of connected participants and notify users when participants join or leave.
- **User Identification:** Each participant must have a username, and the application should visually distinguish the operations performed by different users, for example, through cursor indicators or user-specific colours.
- **Session Continuity:** Users must be able to join or leave an ongoing session without disrupting the other participants. Newly joined users must receive the current whiteboard state.

### 3. Real-Time Communication and Event Handling
- **Operation Transmission:** Client applications must transmit drawing events, erasures, and other whiteboard operations to the server through sockets.
- **Event Broadcasting:** The server must distribute received operations to the other clients so that all participants see updates in near real time.
- **Concurrent Editing:** The system must handle simultaneous operations from multiple users without losing updates or leaving clients with inconsistent canvas states.
- **Ordering and Reliability:** The application must define how operations are ordered and delivered. It must handle delayed, duplicated, or missing updates appropriately to maintain a consistent shared canvas.
- **Connection Management:** The server must detect disconnected clients, manage timeouts, and handle reconnections without terminating the entire session.

### 4. Text Communication and Collaboration
- **Group Chat:** Participants must be able to exchange text messages while collaborating on the whiteboard.
- **Message Broadcasting:** Messages must be sent to the server and distributed to all participants in the session.
- **Chat History:** The interface must display messages chronologically, along with the sender's username and timestamp.

### 5. Whiteboard State Management and Recovery
- **State Representation:** The system must maintain a representation of the whiteboard, strokes and their properties.
- **State Synchronization:** When a new client joins, the server must send the current canvas state so that the client can continue from the existing workspace.
- **Recovery After Disconnection:** A client that reconnects must be able to recover the latest available whiteboard state and resume collaboration.
- **Undo and Redo:** The application must provide undo/redo functionality for whiteboard operations, with clearly defined behaviour when multiple users modify the canvas concurrently.

## Deliverables
- **Server Application:** A standalone executable that can be run on a machine in the LAN to host and manage collaborative whiteboard sessions.
- **Client Application:** A standalone executable that users can run to join sessions and collaborate on the shared whiteboard.
- **Source Code:** Source code along with respective comments for both client and server applications.
- **Technical Documentation (PDF):** A document explaining the system architecture, application-level message format, socket communication, synchronization strategy, concurrency handling, and recovery mechanisms. This document should also contain a User Guide with instructions for configuring the server, connecting clients over the LAN, creating or joining sessions, and using the application.
- **Presentation (PPT):** A presentation highlighting the system architecture, key features, implementation approach, and important design decisions. Include relevant screenshots of the application, workflow diagrams, and examples demonstrating the implemented functionality.
- **Explanation Video:** Each team member must explain their individual contribution and the implementation details of the project using the presentation and source code. The explanation should cover the relevant design choices, key functionalities, and technical implementation. You may use Zoom or a similar meeting application to record the explanation, ensuring that each member's name and face are clearly visible while presenting. A live application demonstration is not required in the video. The focus should be on explaining the implementation using the PPT and source code.

*Zip all the above files into a single file for final submission. Only one team member needs to submit.*

## Demonstration and Evaluation (In Person)
All team members must be present in person for a live demonstration. During the demonstration, teams
must download their submitted application, configure it, and run it successfully with the Internet
connection disabled. All mandatory and additional features will be tested and evaluated. Every team
member must be prepared to explain their contribution and clarify technical queries when asked.

## Evaluation Rubrics
- **Network Architecture:** The system must follow a client-server architecture, with a central server responsible for session management, whiteboard state coordination, and event distribution. All essential communication must operate over TCP/IP sockets on a LAN. No internet connectivity should be required.
- **Network Protocol Design:** Students must define an application-level message format for communicating drawing events, control messages, chat messages, and canvas-state updates. They should justify the choice of transport protocol and explain how message framing and delivery are handled.
- **User Interface:** The application must provide a clean graphical user interface with a shared canvas, drawing toolbar, participant list, chat panel, and session controls.
- **Performance:** The application should minimize synchronization delay and unnecessary network traffic.
- **Concurrency:** The server must support multiple simultaneous clients and process concurrent drawing and editing operations without corrupting the shared state.
- **Security and Access Control:** The application should support basic session access control and prevent unauthorized users from modifying a protected workspace.
- **Platform:** The application may be developed for a specific operating system, such as Linux or Windows, or designed to be cross-platform.

## Evaluation Instructions
1. Implementation of all mandatory core requirements is essential for evaluation.
2. Marks will be awarded based on the completeness, correctness, and quality of the implementation.
3. You are encouraged to implement additional features beyond the minimum requirements.
4. Teams incorporating more features with more functionality and effective integration will be awarded relatively higher marks.

The core requirements are mandatory. You may extend the application with advanced features such as
collaborative diagram creation (lines, shapes), flowcharts, mind maps, sticky notes, image insertion,
presentation mode, role-based editing permissions, version history, offline editing, exporting canvas, etc.
