assembly_task = """
VELOCITY, ACC = 30, 30
OFF, ON = 0, 1

velx, accx = (100, 50)
velj, accj = (20, 20)

set_tool("Tool Weight")
set_tcp("GripperDA_v1")


class AssemblyRobot:
    def __init__(self):
        self.velx = velx
        self.accx = accx
        self.velj = velj
        self.accj = accj

        self.part_pos = posx([401, 146, 55, 0, 180, 0])
        self.before_put_top = posx([479, 59, 86, 90, 170, -90])
        self.before_put_right = posx([587, -46, 84, 0, 170, -90])
        self.before_put_bottom = posx([482, -159, 84, 90, -170, 90])
        self.hub_home = posx([481.03, -53.58, 185.00, 9.76, -179.96, 9.50])

    def grip_close(self):
        set_digital_output(1, 0)
        set_digital_output(2, 1)
        wait(0.5)

    def grip_open(self):
        set_digital_output(1, 1)
        set_digital_output(2, 0)
        wait(0.5)

    def move_relative(self, dx=0, dy=0, dz=0, da=0, db=0, dc=0):
        movel(
            posx([dx, dy, dz, da, db, dc]),
            vel=self.velx,
            acc=self.accx,
            ref=DR_BASE,
            mod=DR_MV_MOD_REL,
        )

    def move_ready(self):
        self.grip_open()
        movej(posj([0, 0, 90, 0, 90, 0]), vel=self.velj, acc=self.accj)
        wait(0.5)

    def move_to_part(self):
        movel(self.part_pos, vel=self.velx, acc=self.accx, ref=DR_BASE)
        wait(0.5)

        self.grip_close()
        wait(1)

        self.move_relative(dz=100)
        wait(0.5)

    def put_top(self):
        self.move_to_part()

        movel(self.before_put_top, vel=self.velx, acc=self.accx, ref=DR_BASE)
        wait(0.5)

        self.move_relative(dy=-50, dz=-8)
        wait(0.5)

        self.grip_open()
        wait(1)

        self.move_relative(dy=50, dz=50)
        wait(0.5)

        movej(posj([0, 0, 90, 0, 90, 0]), vel=self.velj, acc=self.accj)

    def put_right(self):
        self.move_to_part()

        self.move_relative(dx=150)
        wait(0.5)

        movel(self.before_put_right, vel=self.velx, acc=self.accx, ref=DR_BASE)
        wait(0.5)

        self.move_relative(dx=-50, dz=-8)
        wait(0.5)

        self.grip_open()
        wait(1)

        self.move_relative(dx=50, dz=50)
        wait(0.5)

        movej(posj([0, 0, 90, 0, 90, 0]), vel=self.velj, acc=self.accj)

    def put_bottom(self):
        self.move_to_part()

        self.move_relative(dy=-330)
        wait(0.5)

        movel(self.before_put_bottom, vel=self.velx, acc=self.accx, ref=DR_BASE)
        wait(0.5)

        self.move_relative(dy=52, dz=-8)
        wait(0.5)

        self.grip_open()
        wait(1)

        self.move_relative(dy=-50, dz=50)
        wait(0.5)

        movej(posj([0, 0, 90, 0, 90, 0]), vel=self.velj, acc=self.accj)

    def movel_tool(self, dx=0, dy=0, dz=0, da=0, db=0, dc=0):
        pos = posx([dx,dy,dz,da,db,dc])
        movel(pos, vel=self.velj, acc=self.accj, ref=DR_TOOL)
        
    def set_hub(self, est_pos):
        est_pos[0] -= 10
        movel(est_pos, vel=self.velj, acc=self.accj, ref=DR_TOOL)
        self.movel_tool(dz=120)
        self.grip_close()
        self.movel_tool(dz=-120)
        self.movel_tool(da=-est_pos[3],dc=-est_pos[5])
        movel(self.hub_home, vel=self.velj, acc=self.accj, ref=DR_BASE)
        self.movel_tool(dz=120)
        self.grip_open()
        self.movel_tool(dz=-120)
        

    def run(self, est_pos, face_id):
        self.move_ready()
        self.set_hub(est_pos)
        self.move_ready()

        if face_id == 1:
            self.put_top()

        elif face_id == 2:
            self.put_right()

        elif face_id == 3:
            self.put_bottom()

        else:
            tp_log("unsupported face_id")

robot = AssemblyRobot()
robot.run(est_pos, face_id)
"""

scan_pose_task = """
velx, accx = (80, 50)
velj, accj = (20, 20)

set_tool("Tool Weight")
set_tcp("GripperDA_v1")


def move_to_scan_pose():
    tp_log("move_to_scan_pose start")

    # 임의 좌표: 허브를 위쪽/전방에서 바라보기 위한 높은 위치
    # 실제 카메라 위치와 허브 위치에 맞게 나중에 반드시 튜닝 필요
    scan_pose_x = posx([500, 0, 350, 0, 180, 0])

    movej(scan_ready_j, vel=velj, acc=accj)
    wait(0.5)

    movel(scan_pose_x, vel=velx, acc=accx, ref=DR_BASE)
    wait(0.5)

    tp_log("move_to_scan_pose done")


move_to_scan_pose()
"""